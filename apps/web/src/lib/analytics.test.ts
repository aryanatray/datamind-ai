import { describe, expect, it } from "vitest";
import { filterRows } from "../components/analytics";
describe("analytical filters", () => {
  const rows = [{ month: "2026-01-01", customer: "Aster", value: 20 }, { month: "2026-02-01", customer: "Birch", value: 30 }];
  it("filters actual values without regenerating queries", () => { expect(filterRows(rows, "aster", "month", "", "")).toEqual([rows[0]]); });
  it("applies inclusive date bounds", () => { expect(filterRows(rows, "", "month", "2026-02-01", "2026-02-01")).toEqual([rows[1]]); });
  it("preserves original data", () => { filterRows(rows, "none", "month", "", ""); expect(rows).toHaveLength(2); });
});
