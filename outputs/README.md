# Outputs

`outputs/figures/` contains the six figures in the paper. Running the figure stage
regenerates the requested files at these same paths.

| File | Paper result |
| --- | --- |
| `Fig_01.jpg` | Minute-resolution GOES magnetic-field measurements in GSM coordinates |
| `Fig_02.jpg` | Three-month means and 1.1–4.5-year bandpass-filtered GOES series |
| `Fig_03.jpg` | Continuous and global wavelet spectra of the B_GSMx component |
| `Fig_04.jpg` | Continuous and global wavelet spectra of the B_GSMy component |
| `Fig_05.jpg` | Continuous and global wavelet spectra of the B_GSMz component |
| `Fig_06.jpg` | Continuous and global wavelet spectra of the sunspot-number series |

Regenerate Figures 2–6 from the released compact inputs with:

```bash
python scripts/reproduce.py
```

Regenerate all six figures from the complete source chain with:

```bash
python scripts/reproduce.py all --full
```

Generate one figure by passing its paper number, for example:

```bash
python scripts/reproduce.py figure 3
```

Run the default calculation or `python scripts/reproduce.py analyze`
first so the selected and wavelet checkpoints exist. Generating Figure 1
separately requires the prepared one-minute mission files.

To keep Figure 1 rendering bounded in memory, the figure stage reads one
mission at a time. The paper configuration plots every retained one-minute row.
For a faster preview that does not reproduce the paper sampling, set
`figures.minute_row_stride` above `1` in `data/config/paper.toml`; that changes
only plotting, not the prepared one-minute files.

The tracked JPEGs are the published article figures and are covered by the article's
Creative Commons Attribution 4.0 license. See
[`../DATA_NOTICE.md`](../DATA_NOTICE.md) for attribution and reuse terms.

`figures/SHA256SUMS` identifies the tracked published files before they are
replaced by a reproduction run. From that directory, verify the tracked versions
with `shasum -a 256 -c SHA256SUMS` (macOS) or `sha256sum -c SHA256SUMS` (GNU
coreutils).
