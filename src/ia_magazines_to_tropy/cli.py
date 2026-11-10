"""ia2tropy: from an Internet Archive magazine collection to a Tropy collection.

Steps, in order (each takes a profile name, e.g. "byte"):
    fetch      download the cover (first page image) of every item
    prepare    make 2048 px PNGs for the model
    describe   describe each cover with a local or hosted model
    review     review and correct the records in a local web page
    export     write a CSV and images for Tropy's CSV import plugin

Run "ia2tropy <step> --help" for a step's options.
"""
import sys

STEPS = ["fetch", "prepare", "describe", "review", "export"]


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help") or sys.argv[1] not in STEPS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help") else 2)
    step = sys.argv[1]
    module = __import__(f"ia_magazines_to_tropy.{step}", fromlist=["main"])
    module.main(sys.argv[2:])
