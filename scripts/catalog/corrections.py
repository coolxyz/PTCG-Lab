"""Narrow, revision-pinned manual corrections; raw evidence stays immutable."""

import copy


def apply_number_correction(row, code, revision, corrections):
    result = copy.deepcopy(row)
    candidates = [
        c
        for c in corrections
        if c["productCode"] == code
        and c["cardPage"] == row.get("cardPage")
        and c["sourceRow"] == row["sourceRow"]
    ]
    if not candidates:
        return result
    if len(candidates) != 1:
        raise ValueError("Ambiguous number correction")
    c = candidates[0]
    if (
        revision != c["sourceRevision"]
        or row["collectorNumber"] != c["oldNumber"]
        or row["printedNumber"] != c["oldPrintedNumber"]
    ):
        raise ValueError(
            "Number correction source changed; review required: " + c["id"]
        )
    result.update(
        collectorNumber=c["newNumber"],
        printedNumber=c["newPrintedNumber"],
        numberLabel=c["newPrintedNumber"],
        sourceCollectorNumber=row["collectorNumber"],
        numberCorrection=c,
    )
    return result
