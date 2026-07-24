"""
Generate a small, fully synthetic example .mrc file for demos and tests.

All titles, identifiers, and location codes below are invented — there is no
real institutional data here. The 866 holdings strings are format examples
chosen to exercise the parser's supported patterns.

Run:
    python scripts/create_example_mrc.py
    # -> writes data/example_holdings.mrc
"""

import os
from pymarc import Record, Field, Subfield

OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "example_holdings.mrc")

# (title, [866 $a holdings statements]) — invented titles, example holdings
EXAMPLES = [
    ("Journal of Imaginary Studies", [
        "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)",
        "v.6(1995)-",
    ]),
    ("Annals of Fictional Research", [
        "v. 1-14 (1953-1966)",
    ]),
    ("Review of Made-Up Sciences", [
        "v. 8 no. 3-v. 10 no. 2 (1981-Fall 1983)",
        "v. 27 no. 4-v. 31 no. 4 (April 1992-April 1996)",
    ]),
    ("Quarterly of Nonexistent Topics", [
        "34 no 3, 4 (Summer, Autumn 1990)",
        "39 no 1 (Spring 1995)",
    ]),
    ("Bulletin of Placeholder Serials", [
        "v.1(1990)-v.10(1999)",
        "v.12(2001)-v.15(2004)",
    ]),
]


def make_record(n, title, holdings):
    rec = Record()
    rec.leader = "00522cx  a22001453  4500"
    rec.add_field(Field(tag="001", data=f"exmpl{n:04d}"))
    rec.add_field(Field(tag="008", data="1011252u    8  4001uueng0000000"))
    rec.add_field(Field(tag="245", indicators=["0", "0"],
                        subfields=[Subfield(code="a", value=title + ".")]))
    rec.add_field(Field(tag="852", indicators=["8", " "], subfields=[
        Subfield(code="b", value="EXL"),
        Subfield(code="c", value="example-lib"),
        Subfield(code="h", value="Journals"),
    ]))
    for text in holdings:
        rec.add_field(Field(tag="866", indicators=[" ", "0"],
                            subfields=[Subfield(code="a", value=text)]))
    return rec


def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "wb") as fh:
        for i, (title, holdings) in enumerate(EXAMPLES, start=1):
            fh.write(make_record(i, title, holdings).as_marc())
    print(f"Wrote {len(EXAMPLES)} synthetic records to {os.path.relpath(OUTPUT)}")


if __name__ == "__main__":
    main()
