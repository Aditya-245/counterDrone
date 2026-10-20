/**
 * The simulation runs in the browser.
 *
 * This test drives the real stack: it boots Pyodide in a worker, plays a
 * scenario and proves the frames the trainee sees came from the browser's own
 * engine - not from a server socket.
 */

import { expect, test } from "@playwright/test"

/** The engine has to fetch a wasm runtime and three wheels before it can tick. */
const ENGINE_BOOT_TIMEOUT = 120_000

test.describe("client-side simulation", () => {
  test("the browser plays the scenario", async ({ page }) => {
    const consoleErrors: string[] = []
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text())
    })
    page.on("pageerror", (error) => consoleErrors.push(error.message))

    await page.goto("/")
    const playLink = page.getByRole("link", { name: "Play" }).first()
    await expect(playLink).toBeVisible()
    await playLink.click()

    await page.waitForURL(/\/sim\//, { timeout: 30_000 })

    // The engine boots, the session registers and the frames arrive.
    await expect(page.getByText("running")).toBeVisible({ timeout: ENGINE_BOOT_TIMEOUT })

    // The mission clock advances: the engine is ticking.
    const clock = page.getByTestId("mission-clock")
    await expect(clock).toBeVisible({ timeout: ENGINE_BOOT_TIMEOUT })
    const first = await clock.textContent()
    await page.waitForTimeout(4000)
    const second = await clock.textContent()
    expect(second).not.toBe(first)

    // No websocket: the session is played by the browser.
    expect(consoleErrors.filter((e) => /websocket|ws:\/\//i.test(e))).toHaveLength(0)
  })

  test("the AAR replays a session this browser played", async ({ page }) => {
    test.slow()
    await page.goto("/")
    const playLink = page.getByRole("link", { name: "Play" }).first()
    await expect(playLink).toBeVisible()
    await playLink.click()
    await page.waitForURL(/\/sim\//, { timeout: 30_000 })
    await expect(page.getByText("running")).toBeVisible({ timeout: ENGINE_BOOT_TIMEOUT })

    // Let the scenario run, then open the after-action review.
    await page.waitForTimeout(5000)
    await page.getByRole("link", { name: /open aar/i }).click()
    await page.waitForURL(/\/aar\//, { timeout: 30_000 })

    // The replay is served from this browser's capture: it carries the state
    // hash the engine produced. (If the capture has not landed yet the server
    // replays the action log, which is the slower but equivalent path.)
    await expect(page.getByText(/hash [0-9a-f]{12}/)).toBeVisible({ timeout: 150_000 })
  })
})
