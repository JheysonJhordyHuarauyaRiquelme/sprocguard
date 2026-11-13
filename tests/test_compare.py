from sprocguard.compare import compare_run, diff_table, learn_rows, learn_volatility


def test_diff_table_with_primary_key():
    before = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    after = [{"id": 1, "v": "A"}, {"id": 3, "v": "c"}]
    d = diff_table(before, after, ["id"])
    assert d["inserted"] == [{"id": 3, "v": "c"}]
    assert d["deleted"] == [{"id": 2, "v": "b"}]
    assert d["updated"] == [{"key": {"id": 1}, "changes": {"v": ["a", "A"]}}]


def test_diff_table_without_key_is_a_multiset():
    before = [{"v": 1}, {"v": 1}]
    after = [{"v": 1}, {"v": 1}, {"v": 1}, {"v": 2}]
    d = diff_table(before, after, [])
    assert sorted(r["v"] for r in d["inserted"]) == [1, 2]
    assert d["deleted"] == []


def test_learn_rows():
    assert learn_rows([[1, "a"]], [[1, "a"]]) == (set(), True)
    assert learn_rows([[1], [2]], [[2], [1]]) == (set(), False)
    assert learn_rows([[1, "t1"], [2, "t1"]], [[1, "t2"], [2, "t2"]]) == ({1}, True)
    assert learn_rows([[1]], [[1], [2]]) is None


def _run(order_id, date, total, error=None):
    return {
        "error": error, "return_value": 0, "outputs": {"@OrderId": order_id},
        "result_sets": [{"columns": ["OrderId", "Total"], "rows": [[order_id, total]]}],
        "tables": {"dbo.Orders": {"key": ["OrderId"], "updated": [], "deleted": [],
                                  "inserted": [{"OrderId": order_id, "OrderDate": date, "Total": total}]}},
    }


def test_volatility_and_comparison():
    r1, r2 = _run(1000, "t1", "25.00"), _run(1001, "t2", "25.00")
    learned = learn_volatility(r1, r2)
    assert learned["volatile_scalars"] == ["output:@OrderId"]
    assert learned["result_sets"][0]["volatile_columns"] == ["OrderId"]
    assert learned["volatile_table_columns"]["dbo.Orders"] == ["OrderDate", "OrderId"]
    assert learned["problems"] == []

    assert compare_run(r1, learned, _run(1002, "t3", "25.00")) == []

    diffs = compare_run(r1, learned, _run(1003, "t4", "22.50"))
    assert any("Result set #1, row 1: Total" in d and "22.50" in d for d in diffs)
    assert any("dbo.Orders" in d and "22.50" in d for d in diffs)

    diffs = compare_run(r1, learned, {**_run(1004, "t5", "25.00"), "error": "boom", "result_sets": []})
    assert any("now fails" in d for d in diffs)
    assert any("Number of result sets" in d for d in diffs)
