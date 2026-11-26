from __future__ import annotations

import asyncio
import random
from typing import List, Tuple

import numpy as np


def generate_bezier_trajectory(
    start: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 28,
) -> List[Tuple[float, float]]:
    """Generates a natural human-like cubic Bezier mouse trajectory with Fitts's Law deceleration."""
    x0, y0 = start
    x3, y3 = end
    dist = np.hypot(x3 - x0, y3 - y0)
    spread = max(dist * 0.25, 15.0)

    # Control points with natural human arc offset
    x1 = x0 + (x3 - x0) * 0.3 + random.uniform(-spread, spread)
    y1 = y0 + (y3 - y0) * 0.3 + random.uniform(-spread, spread)
    x2 = x0 + (x3 - x0) * 0.7 + random.uniform(-spread * 0.5, spread * 0.5)
    y2 = y0 + (y3 - y0) * 0.7 + random.uniform(-spread * 0.5, spread * 0.5)

    # Non-linear t spacing (eases in, decelerates near target)
    raw_t = np.linspace(0, 1, steps)
    t = 1 - (1 - raw_t) ** 2.2

    points: List[Tuple[float, float]] = []
    for ti in t:
        bx = (1 - ti) ** 3 * x0 + 3 * (1 - ti) ** 2 * ti * x1 + 3 * (1 - ti) * ti**2 * x2 + ti**3 * x3
        by = (1 - ti) ** 3 * y0 + 3 * (1 - ti) ** 2 * ti * y1 + 3 * (1 - ti) * ti**2 * y2 + ti**3 * y3
        points.append((round(float(bx), 2), round(float(by), 2)))
    return points


async def human_move_and_click(
    page,
    selector: str,
    start: Tuple[float, float] = (120.0, 120.0),
    steps: int = 20,
) -> List[Tuple[float, float]]:
    """Moves the mouse along a cubic Bezier trajectory to the element center and clicks."""
    element = page.locator(selector).first
    target: Tuple[float, float] = (320.0, 240.0)
    if hasattr(element, "bounding_box"):
        try:
            bbox = await element.bounding_box()
            if bbox and "x" in bbox and "y" in bbox:
                target = (
                    float(bbox["x"]) + float(bbox.get("width", 40.0)) / 2.0,
                    float(bbox["y"]) + float(bbox.get("height", 20.0)) / 2.0,
                )
        except Exception:
            pass

    trajectory = generate_bezier_trajectory(start, target, steps=steps)
    mouse = getattr(page, "mouse", None)
    if mouse is not None and hasattr(mouse, "move"):
        for x, y in trajectory:
            await mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.002, 0.008))

    await element.click()
    return trajectory


async def human_type_text(page, selector: str, text: str) -> None:
    """Moves mouse via Bezier curve, clicks, and types text with Gaussian inter-keystroke cadence."""
    await human_move_and_click(page, selector)
    for idx, char in enumerate(text):
        delay_ms = max(28.0, random.gauss(92.0, 32.0))
        await page.keyboard.type(char, delay=delay_ms)
        if idx > 0 and idx % random.randint(12, 22) == 0:
            await asyncio.sleep(random.uniform(0.15, 0.38))
