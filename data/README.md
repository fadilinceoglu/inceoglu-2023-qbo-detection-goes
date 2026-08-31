# Data

The repository includes the compact machine-readable inputs needed to run the
wavelet analysis and regenerate Figures 2–6. The complete minute-resolution GOES
archive and orbital-element responses remain external.

```text
data/
├── config/
│   └── paper.toml       # parameters used for the paper calculation
├── source/
│   └── sunspots/       # released WDC-SILSO monthly input
└── processed/
    └── quarterly/      # released three-month GOES inputs
```

Other content under `data/source/` and `data/processed/` is created as needed and
ignored by Git. Source links and terms are in
[`../DATA_NOTICE.md`](../DATA_NOTICE.md).

## Released three-month GOES inputs

`processed/quarterly/` contains one CSV file for each of the 16 missions with
usable magnetometer data between GOES-01 and GOES-17; GOES-04 has no magnetometer
record. The time labels span 1977-08-31 through 2023-07-31 across the mission
files. `goes01_quarterly.csv` contains only its schema because no quarterly row
was retained for that mission.

Each CSV has the following columns:

| Column | Meaning | Unit |
| --- | --- | --- |
| `time` | calendar date labelling the three-month bin | UTC date |
| `b_g` | mean B_GSMx | nT |
| `b_g_std` | sample standard deviation of B_GSMx | nT |
| `b_s` | mean B_GSMy | nT |
| `b_s_std` | sample standard deviation of B_GSMy | nT |
| `b_m` | mean B_GSMz | nT |
| `b_m_std` | sample standard deviation of B_GSMz | nT |

Blank fields represent missing values. The files are the direct input boundary for
the normal reproduction command; the analysis selects the eligible continuous
spans, applies the bandpass filter, and calculates the wavelet results from these
values.

## Source data

The complete acquisition stage writes NCEI files under
`source/legacy/goesNN/*.nc` and `source/high_resolution/goesNN/*.nc`, and writes
mission TLE collections as `source/tle/goesNN.tle`. These directories are not
tracked.

### GOES 1–15 one-minute magnetometer observations

The calculation uses the NCEI one-minute magnetometer record from 1977-08-01
through 2020-03-02. Measurements supplied in the spacecraft EPN frame are
converted to GSM coordinates using orbital elements contemporaneous with each
observation.

The calculation retains a UTC timestamp and the three magnetic-field components
`B_GSMx`, `B_GSMy`, and `B_GSMz`, in nanotesla. Provider fill values, samples that
fail the product quality criteria, and times without the orbital information
needed for conversion are represented as missing values.

After source selection and outlier processing, each mission's one-minute values
are written to `processed/minute/goesNN.csv.gz` with columns `time`, `b_g`, `b_s`,
and `b_m`, corresponding respectively to UTC, B_GSMx, B_GSMy, and B_GSMz.

### High-resolution GOES magnetometer observations

The NCEI high-resolution Level-2 record is used from 1995-07-01 through
2023-07-03. GSM vector measurements are reduced to one-minute means. The resulting
schema is the same UTC timestamp plus `B_GSMx`, `B_GSMy`, and `B_GSMz` in
nanotesla. Provider fill values and samples failing the supplied quality criteria
are represented as missing values.

GOES-08, GOES-10, GOES-11, GOES-12, and GOES-14 use both product families. For
each of these missions, the preparation stage cleans each source separately,
concatenates high-resolution rows before one-minute rows, and performs a stable
sort by time. Duplicate timestamps are retained and therefore each retained row
contributes to the three-month statistics. GOES-09, GOES-13, GOES-15, GOES-16,
and GOES-17 use the high-resolution source only. GOES-01, GOES-02, GOES-03,
GOES-05, GOES-06, and GOES-07 use the one-minute source only. GOES-04 is excluded
because it has no magnetometer record.

The following UTC intervals are excluded before the three-month statistics. Date
literals denote midnight UTC. A square bracket includes its endpoint and a
parenthesis excludes it.

| Mission and source | Retained or excluded timestamps |
| --- | --- |
| GOES-05, all sources | exclude `[1986-01-01, 1986-03-13)` |
| GOES-11, one-minute source | retain timestamps after `2004-01-01` |
| GOES-15, high-resolution source | exclude `[2015-11-10, 2015-11-13]`, `[2016-09-06, 2016-09-10]`, `[2016-09-29, 2016-10-07]`, `[2016-10-18, 2016-10-20]`, and `[2017-09-05, 2017-09-09]` |
| GOES-16, high-resolution source | retain timestamps after `2017-04-12` |
| GOES-17, high-resolution source | exclude `[2021-11-03, 2021-11-04)` |

### Orbital elements

Space-Track supplies two-line element sets identified by mission and element-set
epoch. The conversion stage selects an element set appropriate to each observation
time, requiring its epoch to be within 15 days of the observation, and records
times for which no suitable orbital information is available as missing rather
than interpolating an orbit.

### Monthly sunspot number

`source/sunspots/SN_m_tot_V2.0.txt` is the WDC-SILSO monthly total Sunspot Number,
version 2.0, used by the calculation. It contains 642 monthly rows from 1970-01
through 2023-06. Its six whitespace-separated columns are calendar year, month,
decimal year, monthly mean sunspot number, its monthly standard deviation, and the
number of contributing observations. A sunspot value of `-1` denotes missing data
in the provider format and is converted to a missing value.

## Preparation of the released GOES inputs

The preparation stage writes per-mission one-minute GSM series and three-month
statistics. For each magnetic-field component and source, it calculates the mean
and sample standard deviation, masks values greater than `mean + 4σ`, recalculates
both statistics from the upper-clipped values, and then masks values less than the
recalculated `mean - 4σ`. Missing values are ignored in these calculations.

The preparation stage then:

- treats a three-month bin as full when `b_m` contains at least 98,550 retained
  one-minute values, the fixed 75%-coverage threshold used by the calculation;
- calculates component means and one-standard-deviation spreads.

Starting from those three-month values, the analysis stage:

- selects the longest continuous interval for each component;
- requires an interval of at least 4.5 years; and
- applies the fifth-order zero-phase Butterworth bandpass defined in
  [`config/paper.toml`](config/paper.toml).

The filter coefficients use sample-domain period parameters `4.445` and `18.1`,
corresponding approximately to 1.1 and 4.5 years at three-month cadence.

Missing intervals are not interpolated. The selected missions are GOES-06,
GOES-07, GOES-08, GOES-10, GOES-12, GOES-13, GOES-15, and GOES-17.

For GOES-02 through GOES-17, a sub-threshold bin keeps its time label and stores
blank component values. GOES-01 omits sub-threshold rows, which yields its
schema-only released CSV.

The analysis stage writes continuous-wavelet power, periods, cone-of-influence
coordinates, global-wavelet power, and analytical 95% PyCWT significance
thresholds under an AR(1) red-noise null. These checkpoints supply the figure
stage. It uses a Morlet wavelet with dimensionless frequency 6, scale spacing
`dj = 1/64`, PyCWT scale parameter `J = 640`, and a smallest scale equal to twice
the mean sampling interval.
