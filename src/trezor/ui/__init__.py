import math
import utime
from micropython import const
from trezorui import Display

from trezor import io, loop, res, utils, workflow

display = Display()

# in debug mode, display an indicator in top right corner
if __debug__:

    def debug_display_refresh():
        display.bar(Display.WIDTH - 8, 0, 8, 8, 0xF800)
        display.refresh()

    loop.after_step_hook = debug_display_refresh

# in both debug and production, emulator needs to draw the screen explicitly
elif utils.EMULATOR:
    loop.after_step_hook = display.refresh

# re-export constants from modtrezorui
NORMAL = Display.FONT_NORMAL
BOLD = Display.FONT_BOLD
MONO = Display.FONT_MONO
MONO_BOLD = Display.FONT_MONO_BOLD
SIZE = Display.FONT_SIZE
WIDTH = Display.WIDTH
HEIGHT = Display.HEIGHT


def lerpi(a: int, b: int, t: float) -> int:
    return int(a + t * (b - a))


def rgb(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | ((b & 0xF8) >> 3)


def blend(ca: int, cb: int, t: float) -> int:
    return rgb(
        lerpi((ca >> 8) & 0xF8, (cb >> 8) & 0xF8, t),
        lerpi((ca >> 3) & 0xFC, (cb >> 3) & 0xFC, t),
        lerpi((ca << 3) & 0xF8, (cb << 3) & 0xF8, t),
    )


# import style definitions
from trezor.ui.style import *  # isort:skip


def contains(area: tuple, pos: tuple) -> bool:
    x, y = pos
    ax, ay, aw, ah = area
    return ax <= x <= ax + aw and ay <= y <= ay + ah


def rotate(pos: tuple) -> tuple:
    r = display.orientation()
    if r == 0:
        return pos
    x, y = pos
    if r == 90:
        return (y, WIDTH - x)
    if r == 180:
        return (WIDTH - x, HEIGHT - y)
    if r == 270:
        return (HEIGHT - y, x)


def pulse(delay: int):
    while True:
        # normalize sin from interval -1:1 to 0:1
        yield 0.5 + 0.5 * math.sin(utime.ticks_us() / delay)


async def alert(count: int = 3):
    short_sleep = loop.sleep(20000)
    long_sleep = loop.sleep(80000)
    current = display.backlight()
    for i in range(count * 2):
        if i % 2 == 0:
            display.backlight(BACKLIGHT_MAX)
            yield short_sleep
        else:
            display.backlight(BACKLIGHT_NORMAL)
            yield long_sleep
    display.backlight(current)


async def click() -> tuple:
    touch = loop.wait(io.TOUCH)
    while True:
        ev, *pos = yield touch
        if ev == io.TOUCH_START:
            break
    while True:
        ev, *pos = yield touch
        if ev == io.TOUCH_END:
            break
    return pos


async def backlight_slide(val: int, delay: int = 35000, step: int = 20):
    sleep = loop.sleep(delay)
    current = display.backlight()
    for i in range(current, val, -step if current > val else step):
        display.backlight(i)
        yield sleep


def backlight_slide_sync(val: int, delay: int = 35000, step: int = 20):
    current = display.backlight()
    for i in range(current, val, -step if current > val else step):
        display.backlight(i)
        utime.sleep_us(delay)


def layout(fn):
    async def inner(*args, **kwargs):
        await backlight_slide(BACKLIGHT_DIM)
        slide = backlight_slide(BACKLIGHT_NORMAL)
        try:
            layout = fn(*args, **kwargs)
            # workflow.onlayoutstart(layout)
            loop.schedule(slide)
            display.clear()
            return await layout
        finally:
            loop.close(slide)
            workflow.onlayoutclose(layout)

    return inner


def layout_without_fade(fn):
    async def inner(*args, **kwargs):
        try:
            layout = fn(*args, **kwargs)
            workflow.onlayoutstart(layout)
            return await layout
        finally:
            workflow.onlayoutclose(layout)

    return inner


def header(
    title: str, icon: str = ICON_DEFAULT, fg: int = FG, bg: int = BG, ifg: int = GREEN
):
    if icon is not None:
        display.icon(14, 15, res.load(icon), ifg, bg)
    display.text(44, 35, title, BOLD, fg, bg)


VIEWX = const(6)
VIEWY = const(9)


def grid(
    i: int,
    n_x: int = 3,
    n_y: int = 5,
    start_x: int = VIEWX,
    start_y: int = VIEWY,
    end_x: int = (WIDTH - VIEWX),
    end_y: int = (HEIGHT - VIEWY),
    cells_x: int = 1,
    cells_y: int = 1,
    spacing: int = 0,
):
    w = (end_x - start_x) // n_x
    h = (end_y - start_y) // n_y
    x = (i % n_x) * w
    y = (i // n_x) * h
    return (x + start_x, y + start_y, (w - spacing) * cells_x, (h - spacing) * cells_y)


class Widget:
    tainted = True

    def taint(self):
        self.tainted = True

    def render(self):
        pass

    def touch(self, event, pos):
        pass

    def __iter__(self):
        touch = loop.wait(io.TOUCH)
        result = None
        while result is None:
            self.render()
            event, *pos = yield touch
            result = self.touch(event, pos)
        return result


# widget-like retained UI, without explicit loop


def in_area(area, x, y):
    ax, ay, aw, ah = area
    return ax <= x <= ax + aw and ay <= y <= ay + ah


# render events
RENDER = const(-1234)
REPAINT = const(-1235)


class Control:
    def dispatch(self, event, x, y):
        if event is RENDER:
            self.on_render()
        elif event is io.TOUCH_START:
            self.on_touch_start(x, y)
        elif event is io.TOUCH_MOVE:
            self.on_touch_move(x, y)
        elif event is io.TOUCH_END:
            self.on_touch_end(x, y)
        elif event is REPAINT:
            self.repaint = True

    def on_render(self):
        pass

    def on_touch_start(self, x, y):
        pass

    def on_touch_move(self, x, y):
        pass

    def on_touch_end(self, x, y):
        pass


class Layout(Control):
    async def __iter__(self):
        try:
            await loop.spawn(self.handle_rendering(), self.handle_input())
        except Result as result:
            return result.value

    @layout
    def handle_rendering(self):
        sleep = loop.sleep(10000)
        while True:
            self.dispatch(RENDER, 0, 0)
            yield sleep

    def handle_input(self):
        touch = loop.wait(io.TOUCH)
        while True:
            event, x, y = yield touch
            self.dispatch(event, x, y)


class Result(Exception):
    def __init__(self, value):
        self.value = value
