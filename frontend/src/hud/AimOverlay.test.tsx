/**
 * The aim overlay: the reticle tracks the locked contact and the class chooser
 * records a five-way call.
 *
 * The overlay reads the mutable `aim` singleton inside a rAF loop, so the tests
 * set that state directly and wait for the loop to report it into the DOM - the
 * same path the 3D scene uses.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import AimOverlay from "./AimOverlay"
import { aim, resetAim } from "@/scene/aim"

beforeEach(() => {
  resetAim()
})

describe("AimOverlay", () => {
  it("locks the reticle, opens the chooser on Enter and records the call", async () => {
    const onClassify = vi.fn()
    render(<AimOverlay tracks={[]} onLock={() => {}} onClassify={onClassify} />)

    aim.active = true
    aim.lockedId = "T0001"
    await waitFor(() => expect(screen.getByTestId("reticle")).toHaveClass("locked"))

    fireEvent.keyDown(window, { key: "Enter" })
    expect(screen.getByTestId("class-chooser")).toBeInTheDocument()

    fireEvent.keyDown(window, { key: "1" })
    expect(onClassify).toHaveBeenCalledWith("T0001", "hostile")
    expect(screen.queryByTestId("class-chooser")).not.toBeInTheDocument()
  })

  it("records an unknown call as a real call", async () => {
    const onClassify = vi.fn()
    render(<AimOverlay tracks={[]} onLock={() => {}} onClassify={onClassify} />)

    aim.active = true
    aim.lockedId = "T0007"
    await waitFor(() => expect(screen.getByTestId("reticle")).toHaveClass("locked"))

    fireEvent.keyDown(window, { key: "Enter" })
    fireEvent.keyDown(window, { key: "5" })
    expect(onClassify).toHaveBeenCalledWith("T0007", "unknown")
  })

  it("cancels the chooser with Escape without classifying", async () => {
    const onClassify = vi.fn()
    render(<AimOverlay tracks={[]} onLock={() => {}} onClassify={onClassify} />)

    aim.active = true
    aim.lockedId = "T0002"
    await waitFor(() => expect(screen.getByTestId("reticle")).toHaveClass("locked"))

    fireEvent.keyDown(window, { key: "Enter" })
    expect(screen.getByTestId("class-chooser")).toBeInTheDocument()
    fireEvent.keyDown(window, { key: "Escape" })
    expect(screen.queryByTestId("class-chooser")).not.toBeInTheDocument()
    expect(onClassify).not.toHaveBeenCalled()
  })

  it("opens the chooser when the page asks for it (double click in the scene)", async () => {
    const onClassify = vi.fn()
    const { rerender } = render(
      <AimOverlay tracks={[]} chooserRequest={0} onLock={() => {}} onClassify={onClassify} />,
    )

    aim.active = true
    aim.lockedId = "T0003"
    await waitFor(() => expect(screen.getByTestId("reticle")).toHaveClass("locked"))

    rerender(
      <AimOverlay tracks={[]} chooserRequest={1} onLock={() => {}} onClassify={onClassify} />,
    )
    expect(screen.getByTestId("class-chooser")).toBeInTheDocument()
  })

  it("hides the reticle once the pointer leaves the scene", async () => {
    const { container } = render(
      <AimOverlay tracks={[]} onLock={() => {}} onClassify={() => {}} />,
    )
    aim.active = true
    aim.lockedId = "T0004"
    const reticle = screen.getByTestId("reticle")
    await waitFor(() => expect(reticle).toHaveClass("on"))

    aim.active = false
    await waitFor(() => expect(reticle).not.toHaveClass("on"))
    expect(container.querySelector(".aim-layer")).toBeInTheDocument()
  })
})
