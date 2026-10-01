# INVALID-DRAFT DEMONSTRATION: deliberately corrupted copies of the genuine draft. This is NOT the genuine run.

Source: approved draft of genuine run `20261001-141537`. Each row corrupts ONE thing in that draft.

| corruption | change | verdict | caught as |
|---|---|---|---|
| none (control) | unmodified genuine draft | approve | 0 issues |
| value (x1.3) | "d spend in week 12 was $23,030 [F1], 10.8% higher" -> "d spend in week 12 was $29,939 [F1], 10.8% higher" | reject | [value] $29,939 ['F1'] does not match any number of that finding |
| unit ($ removed) | "ded spend in week 12 was $23,030 [F1], 10.8% highe" -> "ded spend in week 12 was 23,030 [F1], 10.8% higher" | reject | 23,030 ['F1']: [unit] spend_usd must carry $ |
| channel (wrong name) | "## What the numbers mean Google Search CPA in week" -> "## What the numbers mean Email CPA in week 6 was $" | reject | $202.90 ['F4']: [channel] the number is introduced as ['email'] but the finding is about ' |
| period (week moved) | "d Blended spend in week 12 was $23,030 [F1], 10.8%" -> "d Blended spend in week 11 was $23,030 [F1], 10.8%" | reject | $23,030 ['F1']: [period] sentence says [[11], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]] but the |
| direction (higher<->lower) | "was $23,030 [F1], 10.8% higher [F1] than the $20," -> "was $23,030 [F1], 10.8% lower [F1] than the $20,7" | reject | $23,030 ['F1']: [direction] text says down but the data went up |
| metric (CPA<->ROAS etc.) | "ek vs the period Blended spend in week 12 was $23," -> "ek vs the period Blended revenue in week 12 was $2" | reject | $23,030 ['F1']: [metric] sentence does not mention spend_usd (spend/budget) |

## Retry demonstration

Corrupted draft #1 (value (x1.3)) -> reviewer verdicts: reject -> reject -> approve (attempts: 3).
