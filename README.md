# Detection of solar QBO-like signals in earth’s magnetic field from multi-GOES mission data

This repository contains the calculation and figures for:

> Inceoglu, F. & Loto’aniu, P. T. M. (2023). Detection of solar QBO-like
> signals in earth’s magnetic field from multi-GOES mission data.
> *Scientific Reports*, 13, 19460.
> <https://doi.org/10.1038/s41598-023-46902-6>

The tracked outputs are the six figures in the paper. The scripts can reproduce
individual figures, resume from existing calculation checkpoints, or perform the
complete acquisition and analysis chain.

Compact three-month GOES inputs and the monthly sunspot series are included. A
fresh clone can therefore run the wavelet analysis and regenerate Figures 2–6
without first downloading the complete minute-resolution GOES archive.

## Calculation

The analysis:

1. reads GOES magnetometer observations from NOAA NCEI;
2. converts measurements supplied in EPN coordinates to GSM coordinates using
   contemporaneous two-line element sets;
3. forms one-minute values, applies the study's sequential four-standard-deviation
   outlier rule, and calculates three-month means and standard deviations;
4. treats a three-month bin as full when its B_GSMz component contains at least
   98,550 one-minute values (the 75% threshold used by the calculation), selects
   each mission's longest continuous interval, and requires at least 4.5 years of
   data;
5. applies a fifth-order, zero-phase Butterworth bandpass for periods from 1.1 to
   4.5 years; and
6. calculates standardized Morlet continuous and global wavelet spectra with
   analytical 95% significance thresholds under a first-order autoregressive
   (AR(1)) red-noise null using PyCWT's significance calculation.

The eight missions satisfying the selection criteria are GOES-06, GOES-07,
GOES-08, GOES-10, GOES-12, GOES-13, GOES-15, and GOES-17. The complete numerical
configuration is in [`data/config/paper.toml`](data/config/paper.toml).

## Installation

Python 3.9 is recommended.

```bash
git clone https://github.com/fadilinceoglu/inceoglu-2023-qbo-detection-goes.git
cd inceoglu-2023-qbo-detection-goes
python3.9 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The lightweight installation above is sufficient for the normal reproduction
from the included three-month inputs. For `--full`, install the additional raw
acquisition and coordinate-conversion dependencies instead:

```bash
python -m pip install -r requirements-full.txt
```

The complete acquisition stage also needs a
[Space-Track](https://www.space-track.org/) account. Supply its credentials only
through environment variables:

```bash
export SPACETRACK_IDENTITY="your-account-email"
export SPACETRACK_PASSWORD="your-password"
```

Do not place credentials in source files or configuration committed to Git.

## Reproduction commands

Run or resume the complete calculation from the deepest available checkpoint:

```bash
python scripts/reproduce.py
```

On a fresh clone, this command uses the released three-month inputs to run the
analysis and regenerate Figures 2–6. It does not begin the data-intensive GOES
acquisition implicitly. Figure 1 is retained at its tracked published version
on this path.

Authorize the complete chain, including authoritative downloads and regeneration
of Figure 1, with:

```bash
python scripts/reproduce.py --full
```

Run one stage at a time:

```bash
python scripts/reproduce.py --stage acquire
python scripts/reproduce.py --stage prepare --full
python scripts/reproduce.py --stage analyze
python scripts/reproduce.py --stage figures --full
```

Omit `--full` from the preparation and figure stages when working only from the
released three-month checkpoints and regenerating Figures 2–6.

Generate one paper figure:

```bash
python scripts/reproduce.py --stage figures --figure 4
```

The default calculation or the `analyze` stage must complete before a wavelet
figure is generated separately. Figure 1 instead requires the prepared
one-minute mission files.

For a short acquisition-path check, use a two- or three-day interval:

```bash
python scripts/reproduce.py --stage acquire --missions 16 \
  --start 2023-07-01 --end 2023-07-03
```

Add `--force` to recompute the requested outputs instead of reusing an existing
analysis checkpoint. The lightweight path never enters minute preparation;
forcing raw-data preparation requires both `--full` and `--force`. The released
sunspot file is preserved by a full run, including a forced run; refresh that
provider file only with `python scripts/acquire_data.py sunspots --force`.

## Stages and outputs

| Stage | Result |
| --- | --- |
| `acquire` | NCEI GOES source files and Space-Track orbital elements |
| `prepare` | one-minute GSM series and three-month statistics |
| `analyze` | selected continuous spans, bandpass-filtered series, continuous and global wavelet spectra, and AR(1) significance thresholds |
| `figures` | Figures 2–6 by default; Figure 1 as well with `--full` or `--figure 1` |

Running the figure stage writes to the same six paths as the tracked published
figures. See [`data/README.md`](data/README.md) for input roles and
[`outputs/README.md`](outputs/README.md) for the figure mapping.

## Data and terms

The complete minute-resolution GOES archive and Space-Track orbital elements are
not tracked in Git. The repository does include the compact three-month GOES CSV
files and the exact WDC-SILSO monthly series consumed by the normal reproduction
command. Source links, citations, and terms are recorded in
[`DATA_NOTICE.md`](DATA_NOTICE.md).

Repository software is released under the [MIT License](LICENSE). The published
figures and third-party data have separate terms described in
[`DATA_NOTICE.md`](DATA_NOTICE.md).

## Citation

Please cite both the paper and this repository. Citation metadata for the
repository and the preferred paper citation are provided in
[`CITATION.cff`](CITATION.cff).
