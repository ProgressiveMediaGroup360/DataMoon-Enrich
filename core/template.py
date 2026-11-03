"""Standard PMG lead-delivery template.

Headers are reproduced exactly as in Standard_PMG_Lead_Delivery_Template.csv,
including the stray spaces in " Last Name " and "Asset ".
"""

TEMPLATE_COLUMNS = [
    "First Name", " Last Name ", "Email", "Email Status", "Job Title",
    "Job Function", "Job Level", "Company", "Address 1", "Address 2", "City",
    "State", "Zip", "Country", "Phone", "Industry", "Employee Size",
    "Revenue Size", "Website", "Asset ", "Contact Source Link",
    "Date Delivered", "Source ID",
]

# Never filled by us: the receiving team completes these.
BLANK_BY_DESIGN = ["Asset ", "Date Delivered", "Source ID"]

# internal field name -> template header
FIELD_TO_HEADER = {
    "first_name": "First Name",
    "last_name": " Last Name ",
    "email": "Email",
    "email_status": "Email Status",
    "job_title": "Job Title",
    "job_function": "Job Function",
    "job_level": "Job Level",
    "company": "Company",
    "address_1": "Address 1",
    "address_2": "Address 2",
    "city": "City",
    "state": "State",
    "zip": "Zip",
    "country": "Country",
    "phone": "Phone",
    "industry": "Industry",
    "employee_size": "Employee Size",
    "revenue_size": "Revenue Size",
    "website": "Website",
    "linkedin_url": "Contact Source Link",  # Contact Source Link is the LinkedIn URL
}

CONTENT_FIELDS = list(FIELD_TO_HEADER.keys())


def to_template_row(rec: dict) -> dict:
    """Map an internal record to a template row. Blank-by-design columns stay empty."""
    row = {h: "" for h in TEMPLATE_COLUMNS}
    for field, header in FIELD_TO_HEADER.items():
        v = rec.get(field, "")
        row[header] = "" if v is None else str(v)
    for h in BLANK_BY_DESIGN:
        row[h] = ""
    return row
