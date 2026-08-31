# Detection of solar QBO-like signals in earth’s magnetic field from multi-GOES mission data

This repository contains the calculation and six paper figures for:

> Inceoglu, F. & Loto’aniu, P. T. M. (2023). Detection of solar QBO-like
> signals in earth’s magnetic field from multi-GOES mission data.
> *Scientific Reports*, 13, 19460.
> <https://doi.org/10.1038/s41598-023-46902-6>

The code acquires and prepares multi-mission GOES magnetometer observations,
selects the continuous records used by the study, applies the 1.1–4.5-year
bandpass, calculates continuous and global wavelet spectra, and regenerates the
paper figures.

## Results in scope

- `outputs/figures/Fig_01.jpg`: minute-resolution GOES magnetic-field series.
- `outputs/figures/Fig_02.jpg`: three-month means and bandpass-filtered series.
- `outputs/figures/Fig_03.jpg`–`Fig_05.jpg`: component continuous and global
  wavelet spectra.
- `outputs/figures/Fig_06.jpg`: sunspot-number continuous and global wavelet
  spectra.

Compact three-month GOES inputs and the exact monthly sunspot series consumed by
the analysis are included. They are the practical starting point for the normal
reproduction and avoid resource-intensive processing of the complete
minute-resolution archive.

## Environment

The supported reproduction environment is Python 3.10. Install the pinned
dependencies and the repository package with:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

For the normal checkpoint-to-figure path, the smaller core installation is
sufficient:

```bash
python -m pip install -e .
```

The raw-data path additionally requires `python -m pip install -e ".[full]"`.

## Reproduce

From a fresh clone, run:

```bash
python scripts/reproduce.py
```

This verifies the released inputs, runs the selection, filtering, and wavelet
analysis, and regenerates Figures 2–6. Figure 1 remains at its tracked published
version because it requires the complete prepared one-minute archive.

Explicitly authorize the complete source-data chain with:

```bash
python scripts/reproduce.py all --full
```

This downloads the GOES observations and orbital elements, prepares the
one-minute and three-month series, runs the analysis, and regenerates all six
figures. The acquisition and preparation stages over the complete paper interval
are data- and compute-intensive and should run in a persistent environment.

Every stage and figure can also be run separately:

```bash
python scripts/reproduce.py acquire
python scripts/reproduce.py prepare
python scripts/reproduce.py analyze
python scripts/reproduce.py figures
python scripts/reproduce.py figure 4
python scripts/reproduce.py status
```

For a short acquisition-path check, bound the request to a few days and one
mission:

```bash
python scripts/reproduce.py acquire --missions 16 \
  --start 2023-07-01 --end 2023-07-03
```

The individual stage wrappers call the same package implementation. Detailed
commands, stage contracts, options, and credential handling are in
[`docs/REPRODUCTION.md`](docs/REPRODUCTION.md).

## Calculation conventions

The calculation uses:

- one-minute GSM magnetic-field components obtained directly or by converting
  legacy EPN measurements with contemporaneous orbital elements;
- the study’s sequential four-standard-deviation outlier rule;
- three-month bins accepted at the fixed 98,550-sample threshold;
- each component’s longest continuous interval, with a minimum duration of
  4.5 years;
- a fifth-order zero-phase Butterworth bandpass corresponding approximately to
  periods from 1.1 to 4.5 years; and
- standardized Morlet continuous wavelets with analytical 95% significance
  thresholds under an AR(1) red-noise null, calculated with PyCWT.

The selected missions are GOES-06, GOES-07, GOES-08, GOES-10, GOES-12,
GOES-13, GOES-15, and GOES-17. Exact parameters are recorded in
[`data/config/paper.toml`](data/config/paper.toml), and the input-to-output path
is described in [`docs/DATA_PROVENANCE.md`](docs/DATA_PROVENANCE.md).

## Repository contents

```text
src/qbo_detection/       calculation, plotting, and orchestration code
scripts/                 thin complete-chain, stage, and figure entry points
data/config/             paper calculation parameters
data/source/             released SILSO input; downloaded GOES data are ignored
data/processed/          released three-month GOES inputs; runtime data are ignored
outputs/figures/         six tracked paper figures
docs/                    reproduction and data-provenance details
tests/                   focused scientific and execution checks
```

Run the checks with:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

The tests use compact fixtures and do not run the full acquisition or
minute-resolution preparation.

## Citation and terms

Please cite the paper and this repository. Machine-readable metadata are in
[`CITATION.cff`](CITATION.cff).

Repository software is released under the [MIT License](LICENSE). The released
GOES and SILSO inputs and the published figures have separate source and reuse
terms described in [`DATA_NOTICE.md`](DATA_NOTICE.md).
