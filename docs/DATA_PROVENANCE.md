# Data provenance and calculation path

This document traces the released inputs through the transformations that
produce the paper figures. Provider citations, links, and reuse terms are in
[`../DATA_NOTICE.md`](../DATA_NOTICE.md).

## Released calculation boundary

The normal reproduction starts from two tracked input collections:

- `data/processed/quarterly/goesNN_quarterly.csv`: three-month GOES magnetic-field
  statistics for the 16 missions with usable observations; and
- `data/source/sunspots/SN_m_tot_V2.0.txt`: the monthly total Sunspot Number,
  version 2.0, from WDC-SILSO for 1970-01 through 2023-06.

Their `SHA256SUMS` files identify the exact released bytes. These compact inputs
are sufficient for the interval selection, filtering, wavelet analysis, and
Figures 2–6. The full source path produces the quarterly GOES files from the
provider observations and regenerates Figure 1 as well.

## GOES source observations

The complete calculation uses two NOAA NCEI product families.

### GOES 1–15 one-minute record

The one-minute magnetometer archive is used from 1977-08-01 through 2020-03-02.
Measurements supplied in the spacecraft EPN frame are converted to GSM. The
conversion first rotates EPN vectors to ECI using the propagated spacecraft
state and then uses SpacePy for the time-dependent ECI-to-GSM transformation.

Space-Track supplies mission two-line element sets. For each observation, the
calculation requires the selected element-set epoch to be within 15 days; a time
without suitable orbital information remains missing.

### High-resolution record

The high-resolution Level-2 magnetometer archive is used from 1995-07-01 through
2023-07-03. Supplied GSM vectors are reduced to one-minute means. Provider fill
values and samples failing the product quality criteria are represented as
missing values.

GOES-08, GOES-10, GOES-11, GOES-12, and GOES-14 use both product families.
High-resolution rows precede legacy one-minute rows before a stable time sort,
and duplicate timestamps are retained. GOES-09, GOES-13, GOES-15, GOES-16, and
GOES-17 use only the high-resolution family. GOES-01, GOES-02, GOES-03, GOES-05,
GOES-06, and GOES-07 use only the one-minute family. GOES-04 is excluded because
it has no magnetometer record.

## Mission exclusions

The following UTC rules are applied before three-month statistics. Date literals
denote midnight UTC; a square bracket includes the endpoint and a parenthesis
excludes it.

| Mission and source | Rule |
| --- | --- |
| GOES-05, all sources | Exclude `[1986-01-01, 1986-03-13)`. |
| GOES-11, one-minute source | Retain timestamps after `2004-01-01`. |
| GOES-15, high-resolution source | Exclude `[2015-11-10, 2015-11-13]`, `[2016-09-06, 2016-09-10]`, `[2016-09-29, 2016-10-07]`, `[2016-10-18, 2016-10-20]`, and `[2017-09-05, 2017-09-09]`. |
| GOES-16, high-resolution source | Retain timestamps after `2017-04-12`. |
| GOES-17, high-resolution source | Exclude `[2021-11-03, 2021-11-04)`. |

## One-minute preparation

Each prepared mission file has the columns `time`, `b_g`, `b_s`, and `b_m`,
representing UTC, B_GSMx, B_GSMy, and B_GSMz. Magnetic-field units are nanotesla.

The source-wise outlier calculation is sequential for each component:

1. calculate the mean and sample standard deviation while ignoring missing
   values;
2. mask values greater than `mean + 4σ`;
3. recalculate the mean and sample standard deviation from the upper-clipped
   values; and
4. mask values less than the recalculated `mean - 4σ`.

The complete path writes the prepared series to
`data/processed/minute/goesNN.csv.gz`.

## Three-month values

The quarterly calculation assigns retained one-minute rows to consecutive
three-month bins and calculates each component’s mean and sample standard
deviation. A bin is accepted when B_GSMz contains at least 98,550 retained
one-minute samples, the fixed threshold used by the calculation. Sub-threshold
component fields are blank; GOES-01 has a schema-only released file because no
bin was retained.

The CSV columns are:

| Column | Quantity | Unit |
| --- | --- | --- |
| `time` | calendar label for the three-month bin | UTC date |
| `b_g`, `b_g_std` | mean and sample standard deviation of B_GSMx | nT |
| `b_s`, `b_s_std` | mean and sample standard deviation of B_GSMy | nT |
| `b_m`, `b_m_std` | mean and sample standard deviation of B_GSMz | nT |

## Selection and bandpass

For each magnetic-field component, the analysis selects the longest continuous
interval and requires at least 4.5 years. It retains GOES-06, GOES-07, GOES-08,
GOES-10, GOES-12, GOES-13, GOES-15, and GOES-17.

A fifth-order zero-phase Butterworth difference of lowpasses isolates periods
from approximately 1.1 to 4.5 years. The sample-domain periods are `4.445` and
`18.1` at the three-month cadence. Missing intervals are not interpolated.

## Wavelet calculation

Each selected component series and the monthly sunspot series are standardized
before the continuous wavelet transform. The calculation uses a Morlet wavelet
with dimensionless frequency 6, scale spacing `dj = 1/64`, PyCWT scale parameter
`J = 640`, and a smallest scale equal to twice the mean sampling interval.

The saved products contain continuous-wavelet power, periods, cone-of-influence
coordinates, global-wavelet power, and analytical 95% significance thresholds
under a first-order autoregressive red-noise null. Local significance uses
PyCWT’s `sigma_test = 0` calculation. Global significance uses
`sigma_test = 1` with `dof = N - scales`, where `N` is the series length.

Wavelet checkpoints record the identities of their numerical inputs,
configuration, analysis implementation, PyCWT version, and PyCWT source so that
the figure stage does not silently consume incompatible results.

## Figure mapping

| Output | Inputs |
| --- | --- |
| `Fig_01.jpg` | prepared one-minute GOES component series |
| `Fig_02.jpg` | released three-month values, selected intervals, and bandpass outputs |
| `Fig_03.jpg` | B_GSMx selected series and wavelet products |
| `Fig_04.jpg` | B_GSMy selected series and wavelet products |
| `Fig_05.jpg` | B_GSMz selected series and wavelet products |
| `Fig_06.jpg` | WDC-SILSO series and sunspot wavelet products |

All executable parameters and paths are recorded in
[`../data/config/paper.toml`](../data/config/paper.toml).
