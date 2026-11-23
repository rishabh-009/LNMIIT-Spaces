import re

from app.coursecatalog import branch_names, programmes

#AI SLOP

student_email_domains = ("lnmit.ac.in", "lnmiit.ac.in")
program_names = {
    "U": "Undergraduate",
    "D": "Dual degree",
    "M": "Masters",
    "P": "PhD",
}



def student_identity(email):
    if not isinstance(email, str) or email.count("@") != 1:
        return None
    local, domain = email.strip().split("@", 1)
    if domain.lower() not in student_email_domains:
        return None
    roll_number = local.upper()
    match = re.fullmatch(r"([0-9]{2})([A-Z]{3,5})([0-9]{3})", roll_number)
    if match is None:
        return None
    year, code, serial = match.groups()
    course = next((item for item in programmes if item["prefix"] == code), None)
    program = code[0] if len(code) == 3 and code[0] in program_names else ""
    branch = code[1:] if program else ""
    identity = {
        "roll_number": roll_number,
        "email_local_part": local,
        "enrollment_year": year,
        "program_code": program,
        "program_name": program_names.get(program, ""),
        "branch_code": branch,
        "branch_name": branch_names.get(branch, ""),
        "serial_number": serial,
        "course": "",
        "school": "",
        "mapping_source": "unmapped",
    }
    if course is not None:
        identity.update({
            "course": course["degree"], "branch_name": course["branch"],
            "school": course.get("school", ""), "mapping_source": course["prefix_source"],
        })
    if code == "IMAI":
        identity.update({
            "program_code": "IMAI", "program_name": "Integrated Masters in AI",
            "branch_code": "AI",
        })

    return identity
