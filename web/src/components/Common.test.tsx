import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { QualityRate, RevisionDiff } from "./Common";

describe("evidence and quality boundaries", () => {
  it("shows no percentage for zero valid samples", () => {
    render(<QualityRate yes={0} no={0} unknown={3} />);
    expect(screen.getByText(/待采样/)).toBeInTheDocument();
    expect(screen.queryByText(/0\.0%/)).not.toBeInTheDocument();
    expect(screen.getByText(/未知 3/)).toBeInTheDocument();
  });
  it("excludes unknown labels from the denominator", () => {
    render(<QualityRate yes={1} no={3} unknown={8} />);
    expect(screen.getByText(/25\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/有效标注 4/)).toBeInTheDocument();
  });
  it("does not invent an unavailable prior revision", () => {
    render(<RevisionDiff oldText={null} newText={{ rule: "记录失败类别" }} />);
    expect(screen.getByText("旧版本不可用")).toBeInTheDocument();
    expect(screen.getByText(/记录失败类别/)).toBeInTheDocument();
  });
});
