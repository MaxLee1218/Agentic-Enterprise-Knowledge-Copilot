# Preserved Supplier Quality baseline and post-fix regression

| Observation | Original first population | Post-fix population |
|---|---:|---:|
| Qualified reports | 4/9 | 9/9 |
| Numeric | 175/181 | 181/181 |
| Enumerated fact support | 128/146 | 146/146 |
| P10 rejection | 1/1 | 1/1 |
| First report USD cost / qualified | 0.003927726 | 0.001482535333333333333333333333 |
| Qualified latency median ms | 5371.5 | 6186.0 |

Regression USD ceiling: 0.015365262; aggregate USD: 0.036202128; aggregate EUR upper: 0.056587.
Partial: False; unrun: (); pending: ().
Code revisions: 3c4bf61443c2fe51b9cea955d756e017cdab125f -> 482cdce79424155bc3da882de21b9f0537b8bda5.

## First-case outcomes

| Case | Before qualified | After qualified | Remaining failed checks |
|---|---|---|---|
| P01 | True | True |  |
| P02 | True | True |  |
| P03 | True | True |  |
| P04 | False | True |  |
| P05 | True | True |  |
| P06 | False | True |  |
| P07 | False | True |  |
| P08 | False | True |  |
| P09 | False | True |  |
| P10 | False | False |  |

## Limitations

- Same fixed synthetic scenarios used during debugging; not held-out generalization.
- All first results retained; no repeat replaces an original outcome.
- Latency medians cover different qualified populations; no speedup percentage is inferred.
- USD uses pinned usage ceilings; EUR includes contingencies, not invoice FX or tax.
- Human effort remains assumed; no observed human-time saving is measured.
- Original P01 was calibrated on an earlier revision; code revisions are disclosed per run.
