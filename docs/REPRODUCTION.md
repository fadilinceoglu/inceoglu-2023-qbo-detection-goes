# Reproduction commands

Run all commands from the repository root. `scripts/reproduce.py` is the common
entry point; the smaller scripts dispatch to the same package stages.

## Install

The supported environment is Python 3.10. Install the exact direct dependencies
for the complete calculation and tests with:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

For analysis and plotting from the released three-month inputs:

```bash
python -m pip install -e .
```

For acquisition and minute-resolution preparation without the lock file:

```bash
python -m pip install -e ".[full]"
```

Install the test dependency with:

```bash
python -m pip install -e ".[test]"
```

## Normal reproduction

```bash
python scripts/reproduce.py
```

The no-argument command follows the released checkpoint path:

1. verify all tracked three-month GOES inputs and the tracked SILSO series;
2. select the study intervals and apply the bandpass filter;
3. calculate or reuse compatible wavelet checkpoints; and
4. regenerate Figures 2–6 at their tracked output paths.

The command never enters minute-resolution preparation. Figure 1 is retained at
its tracked published version on this path.

Use `--force` to recalculate wavelet checkpoints before plotting:

```bash
python scripts/reproduce.py all --force
```

## Complete source-data reproduction

```bash
python scripts/reproduce.py all --full
```

The full command acquires the source observations and orbital elements, prepares
the one-minute GSM records and three-month values, performs the analysis, and
regenerates Figures 1–6. The first two parts span the complete multi-mission
record. Run them in a persistent environment with adequate working storage.

The legacy coordinate-conversion path obtains two-line element sets from
[Space-Track](https://www.space-track.org/). Supply credentials at run time:

```bash
export SPACETRACK_IDENTITY="your-account-email"
export SPACETRACK_PASSWORD="your-password"
```

Credentials are never read from the paper configuration or written to tracked
files. `.env.example` contains empty variable names only.

To deliberately replace existing outputs throughout the expensive path, use:

```bash
python scripts/reproduce.py all --full --force
```

## Inspect local state

```bash
python scripts/reproduce.py status
```

The command reports the availability of the released inputs, prepared series,
wavelet checkpoints, and paper figures without changing files.

## Individual stages

```bash
# Download the configured GOES products, orbital elements, and SILSO series.
python scripts/reproduce.py acquire

# Verify the released three-month checkpoint boundary.
python scripts/reproduce.py prepare

# Rebuild one-minute and three-month values from downloaded source data.
python scripts/reproduce.py prepare --full

# Select intervals, filter the series, and calculate wavelet products.
python scripts/reproduce.py analyze

# Regenerate Figures 2–6.
python scripts/reproduce.py figures

# Regenerate Figures 1–6 when the prepared minute data are available.
python scripts/reproduce.py figures --full

# Regenerate one paper figure.
python scripts/reproduce.py figure 4
```

The equivalent role-specific wrappers are:

```bash
python scripts/acquire_goes_data.py
python scripts/prepare_goes_data.py
python scripts/analyze_qbo_signals.py
python scripts/plot_figure_01.py
python scripts/plot_figure_02.py
python scripts/plot_figure_03.py
python scripts/plot_figure_04.py
python scripts/plot_figure_05.py
python scripts/plot_figure_06.py
```

The preparation wrapper uses the released checkpoint path by default; pass
`--full` to rebuild it from the source observations.

## Lower-level substages

After installing the package, the operations inside each top-level stage remain
independently callable:

```bash
# Source families.
python -m qbo_detection.acquisition legacy
python -m qbo_detection.acquisition highres
python -m qbo_detection.acquisition tle
python -m qbo_detection.acquisition sunspots

# Preparation boundaries.
python -m qbo_detection.preparation minute
python -m qbo_detection.preparation quarterly

# Analysis boundaries.
python -m qbo_detection.analysis select-filter
python -m qbo_detection.analysis wavelets

# One figure or the complete six-figure set.
python -m qbo_detection.figures 4
python -m qbo_detection.figures all
```

The `minute` and source-family commands operate on the full configured inputs
unless their module options restrict the request. Use `--help` on a module to
inspect its stage-specific path, date, mission, and force options.

## Bounded acquisition check

A short request exercises the upstream download path without starting the full
interval:

```bash
python scripts/reproduce.py acquire --missions 16 \
  --start 2023-07-01 --end 2023-07-03
```

The date bounds restrict the configured product intervals. They do not extend a
product outside its documented coverage.

## Stage contracts

1. `acquire` writes provider files under `data/source/legacy/` and
   `data/source/high_resolution/`, mission orbital elements under
   `data/source/tle/`, and a local acquisition manifest.
2. `prepare --full` writes one-minute GSM files under
   `data/processed/minute/` and three-month statistics under
   `data/processed/quarterly/`. The normal `prepare` stage only verifies the
   tracked three-month inputs.
3. `analyze` writes selected continuous component series under
   `data/processed/selected/` and continuous/global wavelet checkpoints under
   `outputs/work/`.
4. `figures` writes JPEG files directly to `outputs/figures/`. A requested
   figure replaces the corresponding tracked paper-figure path.

Generated source files, minute data, selected series, and wavelet checkpoints
are ignored by Git. The compact released inputs and paper figures are tracked.

## Shared options

| Option | Meaning |
| --- | --- |
| `--config PATH` | Calculation configuration; defaults to `data/config/paper.toml`. |
| `--source-dir PATH` | GOES, TLE, and SILSO source-data root. |
| `--processed-dir PATH` | One-minute, three-month, and selected-series root. |
| `--wavelet-dir PATH` | Wavelet-checkpoint destination or input. |
| `--figures-dir PATH` | Figure destination. |
| `--missions LIST` | Mission numbers or ranges, such as `6-8,10,12`. |
| `--start DATE`, `--end DATE` | Inclusive UTC bounds for a short acquisition run. |
| `--dpi INTEGER` | Figure resolution; default 600. |
| `--force` | Recalculate or replace outputs for the selected stage. |
| `--full` | Permit the source-data preparation path and Figure 1. |

## Checks

The checksum manifests identify the released quarterly GOES files, SILSO input,
and published figures. Run the focused tests with:

```bash
python -m pytest
```

The tests check input identities and schemas, coordinate equations, interval
selection, filtering, wavelet significance behavior, checkpoint provenance,
figure inputs, and command dispatch. They use small fixtures and do not start
the full acquisition or preparation.
