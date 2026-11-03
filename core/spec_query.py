"""Turn a spec form into a read-only T-SQL query against PMG_DWH.

The query is run through the Fabric Spark connector's passthrough
(spark.read.option(Constants.DatabaseName, "PMG_DWH").synapsesql(<query>)),
which takes no bind parameters - so every value is validated and escaped here,
column names come from a fixed allow-list, and nothing the user types is used
as SQL structure.

Differences from the original query: no DISTINCT (one row per email/phone/job
combination is passed to enrichment, distinct happens at output); company
address is Address 1; at most N *people* are returned; people without an
email are excluded (inner join on DimEmail).
"""
import re

# filter name -> (column, how). Column names are fixed here, never user input.
FILTERS = {
    "job_level": ("dj.job_title_levels", "in"),
    "job_function": ("dj.job_title_role", "in"),
    "industry": ("dc.Industry", "in"),
    "employee_size": ("dc.Company_Size", "in"),
    "revenue_size": ("dc.Company_Revenue", "in"),
    "country": ("dc.Company_Country", "in"),
    "state": ("dc.Company_State", "in"),
    "title_keywords": ("dj.job_title", "like"),
}

MAX_VALUES_PER_FILTER = 50
MAX_VALUE_LEN = 100
_FORBIDDEN = re.compile(r"(;|--|/\*|\*/|[\x00-\x1f])")
_ALLOWED = re.compile(r"^[\w\s&/+.,'’()$<>%:\-]+$", re.UNICODE)


class SpecError(ValueError):
    pass


def _clean(value, like=False) -> str:
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s:
        raise SpecError("empty filter value")
    if len(s) > MAX_VALUE_LEN:
        raise SpecError("filter value too long")
    if _FORBIDDEN.search(s) or not _ALLOWED.match(s):
        raise SpecError(f"filter value contains unsupported characters: {s[:30]!r}")
    if like and re.search(r"[%_\[\]]", s):
        raise SpecError("keywords may not contain % _ [ ]")
    return s.replace("'", "''")


def validate_spec(spec: dict, max_rows: int) -> dict:
    """Return a cleaned spec {"filters": {name: [values]}, "row_limit": int} or raise SpecError."""
    raw = spec.get("filters") or {}
    unknown = set(raw) - set(FILTERS)
    if unknown:
        raise SpecError(f"unknown filter(s): {sorted(unknown)}")
    filters = {}
    for name, values in raw.items():
        if isinstance(values, str):
            values = [v for v in values.split(",")]
        values = [v for v in (str(x).strip() for x in values) if v]
        if not values:
            continue
        if len(values) > MAX_VALUES_PER_FILTER:
            raise SpecError(f"too many values for {name}")
        like = FILTERS[name][1] == "like"
        filters[name] = [_clean(v, like) for v in values]
    if not filters:
        raise SpecError("add at least one filter")
    try:
        limit = int(spec.get("row_limit") or max_rows)
    except (TypeError, ValueError):
        raise SpecError("row_limit must be a number")
    return {"filters": filters, "row_limit": max(1, min(limit, max_rows))}


def build_query(spec: dict, max_rows: int) -> str:
    s = validate_spec(spec, max_rows)
    where = []
    for name, values in s["filters"].items():
        col, how = FILTERS[name]
        if how == "in":
            where.append(f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")")
        else:
            where.append("(" + " OR ".join(f"{col} LIKE '%{v}%'" for v in values) + ")")
    where_sql = " AND ".join(where)
    n = s["row_limit"]
    return f"""
WITH picked AS (
    SELECT TOP ({n}) dp.[id]
    FROM [dbo].[DimPrimary] dp
    JOIN [dbo].[DimEmail] de ON de.[id] = dp.[id]
    LEFT JOIN [dbo].[DimCompany] dc ON dc.company_id = dp.id
    LEFT JOIN [dbo].[DimJob] dj ON dj.id = dp.id
    WHERE {where_sql}
    GROUP BY dp.[id]
    ORDER BY dp.[id]
)
SELECT
     dp.[id]                                             AS person_id
    ,dp.[first_name]                                     AS first_name
    ,dp.[last_name]                                      AS last_name
    ,COALESCE(de.[work_email], de.[emails_address])      AS email
    ,dj.job_title                                        AS job_title
    ,dj.job_title_role                                   AS job_function
    ,dj.job_title_levels                                 AS job_level
    ,dc.Company_Name                                     AS company
    ,dc.Company_Address                                  AS address_1
    ,dc.Company_City                                     AS city
    ,dc.Company_State                                    AS state
    ,da.street_addresses_postal_code                     AS zip
    ,dc.Company_Country                                  AS country
    ,COALESCE(dph.[mobile_phone], dph.[phone_numbers])   AS phone
    ,dc.Industry                                         AS industry
    ,dc.Company_Size                                     AS employee_size
    ,dc.Company_Revenue                                  AS revenue_size
    ,dc.[Updated_Company_Web]                            AS website
    ,dp.[linkedin_url]                                   AS linkedin_url
    ,dc.[Technologies]                                   AS technologies
FROM picked
JOIN [dbo].[DimPrimary] dp ON dp.[id] = picked.[id]
LEFT JOIN [dbo].[DimAddress] da ON da.[id] = dp.[id]
LEFT JOIN [dbo].[DimCompany] dc ON dc.company_id = dp.id
LEFT JOIN [dbo].[DimJob] dj ON dj.id = dp.id
JOIN [dbo].[DimEmail] de ON de.[id] = dp.[id]
LEFT JOIN [dbo].[DimPhone] dph ON dph.[id] = dp.[id]
""".strip()
