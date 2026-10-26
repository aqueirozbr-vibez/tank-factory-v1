import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { focusedRow, moveRow, openRow, rowsOnScreen } from "./KeyboardMap";

afterEach(cleanup);

function Rows() {
  return (
    <table>
      <tbody>
        <tr data-row tabIndex={0} data-testid="r1">
          <td>
            <a href="https://example.test/tasks/a">#1</a>
          </td>
        </tr>
        <tr data-row tabIndex={0} data-testid="r2">
          <td>
            <button type="button">open</button>
          </td>
        </tr>
        <tr data-row tabIndex={0} data-testid="r3">
          <td>no control</td>
        </tr>
      </tbody>
    </table>
  );
}

describe("row keys", () => {
  it("moves focus between the marked rows and stops at the ends", () => {
    render(<Rows />);
    expect(rowsOnScreen()).toHaveLength(3);
    expect(moveRow(1)?.getAttribute("data-testid")).toBe("r1");
    expect(moveRow(1)?.getAttribute("data-testid")).toBe("r2");
    expect(moveRow(1)?.getAttribute("data-testid")).toBe("r3");
    expect(moveRow(1)?.getAttribute("data-testid")).toBe("r3");
    expect(moveRow(-1)?.getAttribute("data-testid")).toBe("r2");
    expect(focusedRow()?.getAttribute("data-testid")).toBe("r2");
  });

  it("opens a row's first link or button on Enter and reports when there is none", () => {
    render(<Rows />);
    let clicked = "";
    screen.getByRole("link", { name: "#1" }).addEventListener("click", (event) => {
      event.preventDefault();
      clicked = "link";
    });
    screen.getByRole("button", { name: "open" }).addEventListener("click", () => {
      clicked = "button";
    });
    expect(openRow(screen.getByTestId("r1"))).toBe(true);
    expect(clicked).toBe("link");
    expect(openRow(screen.getByTestId("r2"))).toBe(true);
    expect(clicked).toBe("button");
    expect(openRow(screen.getByTestId("r3"))).toBe(false);
    fireEvent.keyDown(window, { key: "j" });
  });
});
