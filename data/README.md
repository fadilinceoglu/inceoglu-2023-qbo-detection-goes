# Data

The repository includes the compact machine-readable inputs needed to run the
analysis and regenerate Figures 2–6. The complete minute-resolution GOES archive
and orbital-element responses are acquired only by the full reproduction path.

```text
data/
├── config/
│   └── paper.toml
├── source/
│   └── sunspots/
│       ├── SN_m_tot_V2.0.txt
│       └── SHA256SUMS
└── processed/
    └── quarterly/
        ├── goes01_quarterly.csv ... goes17_quarterly.csv
        └── SHA256SUMS
```

Other content under `data/source/` and `data/processed/` is created locally and
ignored by Git.

## Released three-month GOES inputs

`processed/quarterly/` contains one CSV for each of the 16 missions with usable
magnetometer observations between GOES-01 and GOES-17; GOES-04 has no
magnetometer record. The combined time coverage is 1977-08-31 through
2023-07-31. GOES-01 contains only its schema because no three-month row met the
calculation’s retained-bin rule.

| Column | Quantity | Unit |
| --- | --- | --- |
| `time` | calendar label for the three-month bin | UTC date |
| `b_g`, `b_g_std` | mean and sample standard deviation of B_GSMx | nT |
| `b_s`, `b_s_std` | mean and sample standard deviation of B_GSMy | nT |
| `b_m`, `b_m_std` | mean and sample standard deviation of B_GSMz | nT |

Blank fields represent missing values. The normal reproduction verifies these
files against `processed/quarterly/SHA256SUMS` before using them.

## Released sunspot input

`source/sunspots/SN_m_tot_V2.0.txt` is the WDC-SILSO monthly total Sunspot
Number, version 2.0, consumed by the calculation. It contains 642 monthly rows
from 1970-01 through 2023-06. Its columns are calendar year, month, decimal year,
monthly mean sunspot number, monthly standard deviation, and number of
contributing observations. A value of `-1` denotes a missing sunspot number.

The normal reproduction verifies this file against
`source/sunspots/SHA256SUMS`.

## Full source path

The complete acquisition writes NOAA NCEI files below
`source/legacy/goesNN/` and `source/high_resolution/goesNN/`, and writes mission
orbital elements below `source/tle/`. Preparation writes one-minute GSM series
below `processed/minute/` before calculating the three-month files.

The complete causal path, coordinate rules, exclusions, transformations, and
wavelet products are documented in
[`../docs/DATA_PROVENANCE.md`](../docs/DATA_PROVENANCE.md). Provider links,
citations, and terms are in [`../DATA_NOTICE.md`](../DATA_NOTICE.md).
