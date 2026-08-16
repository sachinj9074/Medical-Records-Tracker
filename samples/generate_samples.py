"""Generate the synthetic, committed sample documents and their eval labels.

All documents are clearly fictional. Running this script is deterministic: it
renders four PNGs into samples/ and writes a matching ground-truth label into
eval/eval_set/ for each. Images and labels come from one source of truth (the
DOCS list) so they cannot drift apart.

Samples (mapped to the project's own example conditions):
  1. Typed prescription      - acute pharyngitis (clean baseline)
  2. Handwritten prescription - tooth pain (the hard case)
  3. Typed lab report         - diabetes panel (printed-flag rule)
  4. Typed clinic note        - rash above the eye (topical meds)

Usage:  python samples/generate_samples.py
"""

import json
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES_DIR = os.path.join(ROOT, "samples")
LABELS_DIR = os.path.join(ROOT, "eval", "eval_set")

FONTS = "C:/Windows/Fonts/"
ARIAL = FONTS + "arial.ttf"
ARIALBD = FONTS + "arialbd.ttf"
INK = FONTS + "Inkfree.ttf"

W, H = 1000, 1414  # A4-ish portrait


def font(path, size):
    return ImageFont.truetype(path, size)


def wrapped(dr, x, y, text, fnt, maxw, fill="black", lh=28):
    line = ""
    for word in text.split():
        test = (line + " " + word).strip()
        if dr.textlength(test, font=fnt) <= maxw:
            line = test
        else:
            dr.text((x, y), line, font=fnt, fill=fill)
            y += lh
            line = word
    if line:
        dr.text((x, y), line, font=fnt, fill=fill)
        y += lh
    return y


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------
def render_typed_rx(d):
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    y = 60
    dr.text((60, y), d["clinic"], font=font(ARIALBD, 40), fill="black"); y += 52
    dr.text((60, y), d["clinic_addr"], font=font(ARIAL, 18), fill=(70, 70, 70)); y += 32
    dr.text((60, y), f'{d["provider_name"]}   {d["provider_qual"]}', font=font(ARIALBD, 22), fill="black"); y += 28
    dr.text((60, y), d["reg"], font=font(ARIAL, 16), fill=(100, 100, 100)); y += 28
    dr.line((60, y, W - 60, y), fill="black", width=2); y += 22
    dr.text((W - 300, y), f'Date: {d["date_display"]}', font=font(ARIAL, 20), fill="black")
    dr.text((60, y), f'Patient: {d["patient_name"]}    Age/Sex: {d["patient_age"]}/{d["patient_sex"]}',
            font=font(ARIAL, 20), fill="black"); y += 46
    dr.text((60, y), f'Diagnosis: {d["diagnosis"]}', font=font(ARIALBD, 22), fill="black"); y += 50
    dr.text((60, y), "Rx", font=font(ARIALBD, 40), fill="black"); y += 58
    for i, m in enumerate(d["meds"], 1):
        head = f'{i}.  {m["form_prefix"]}{m["name"]} {m["strength"]}'.strip()
        sig = f'{m["dose"]} {m["frequency"]} x {m["duration"]}'
        dr.text((90, y), head, font=font(ARIAL, 24), fill="black"); y += 32
        dr.text((120, y), sig, font=font(ARIAL, 20), fill=(60, 60, 60)); y += 42
    y += 12
    y = wrapped(dr, 60, y, f'Advice: {d["advice"]}', font(ARIAL, 20), W - 120); y += 10
    y = wrapped(dr, 60, y, f'Follow-up: {d["follow_up"]}', font(ARIAL, 20), W - 120); y += 70
    dr.text((W - 340, y), "Signature: ______________", font=font(ARIAL, 20), fill="black")
    dr.text((W - 340, y + 30), d["provider_name"], font=font(ARIAL, 18), fill=(70, 70, 70))
    return img


def render_lab_report(d):
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    y = 60
    dr.text((60, y), d["clinic"], font=font(ARIALBD, 40), fill="black"); y += 52
    dr.text((60, y), d["clinic_addr"], font=font(ARIAL, 18), fill=(70, 70, 70)); y += 34
    dr.line((60, y, W - 60, y), fill="black", width=2); y += 20
    dr.text((60, y), f'Patient: {d["patient_name"]}    Age/Sex: {d["patient_age"]}/{d["patient_sex"]}',
            font=font(ARIAL, 20), fill="black")
    dr.text((W - 300, y), f'Date: {d["date_display"]}', font=font(ARIAL, 20), fill="black"); y += 30
    dr.text((60, y), f'Referred by: {d["referred_by"]}', font=font(ARIAL, 20), fill="black"); y += 44
    dr.text((60, y), d["report_title"], font=font(ARIALBD, 24), fill="black"); y += 44
    # table
    cols = [60, 430, 560, 700, 900]  # Test, Result, Unit, Ref range, Flag
    dr.line((60, y, W - 60, y), fill=(120, 120, 120), width=1); y += 8
    hf = font(ARIALBD, 19)
    for x, label in zip(cols, ["Test", "Result", "Unit", "Reference Range", "Flag"]):
        dr.text((x, y), label, font=hf, fill="black")
    y += 30
    dr.line((60, y, W - 60, y), fill=(120, 120, 120), width=1); y += 10
    bf = font(ARIAL, 19)
    for inv in d["investigations"]:
        dr.text((cols[0], y), inv["name"], font=bf, fill="black")
        dr.text((cols[1], y), inv["value"], font=bf, fill="black")
        dr.text((cols[2], y), inv["unit"], font=bf, fill="black")
        dr.text((cols[3], y), inv["reference_range"], font=bf, fill="black")
        dr.text((cols[4], y), inv["printed_flag"], font=font(ARIALBD, 19), fill="black")
        y += 34
    y += 6
    dr.line((60, y, W - 60, y), fill=(120, 120, 120), width=1); y += 30
    dr.text((60, y), "H = above printed reference range.", font=font(ARIAL, 16), fill=(100, 100, 100)); y += 60
    dr.text((W - 340, y), "Verified by: ______________", font=font(ARIAL, 20), fill="black")
    return img


def render_handwritten_rx(d):
    img = Image.new("RGB", (W, H), (252, 251, 246))
    dr = ImageDraw.Draw(img)
    ink = (25, 25, 55)
    y = 70
    dr.text((60, y), d["clinic"], font=font(INK, 46), fill=ink); y += 62
    dr.text((60, y), f'{d["provider_name"]}   {d["provider_qual"]}', font=font(INK, 34), fill=ink); y += 46
    dr.line((60, y, W - 60, y), fill=(90, 90, 90), width=2); y += 28
    dr.text((60, y), f'Date: {d["date_display"]}', font=font(INK, 34), fill=ink); y += 50
    dr.text((60, y), f'{d["patient_name"]}    {d["patient_age"]}/{d["patient_sex"]}', font=font(INK, 34), fill=ink); y += 54
    dr.text((60, y), f'Dx: {d["dx_written"]}', font=font(INK, 40), fill=ink); y += 66
    dr.text((60, y), "Rx", font=font(INK, 54), fill=ink); y += 66
    for line in d["rx_written"]:
        dr.text((90, y), line, font=font(INK, 40), fill=ink); y += 58
    y += 24
    dr.text((60, y), d["advice_written"], font=font(INK, 34), fill=ink); y += 52
    dr.text((60, y), d["follow_written"], font=font(INK, 34), fill=ink); y += 90
    dr.text((W - 300, y), d["provider_name"], font=font(INK, 40), fill=ink)
    return img.rotate(-1.5, expand=False, fillcolor=(252, 251, 246))


RENDERERS = {
    "typed_rx": render_typed_rx,
    "lab": render_lab_report,
    "handwritten_rx": render_handwritten_rx,
}


# --------------------------------------------------------------------------
# Documents (single source of truth for both image and label)
# --------------------------------------------------------------------------
DOCS = [
    {
        "sample_id": "sample_01_pharyngitis",
        "kind": "typed_rx",
        "document_type": "prescription",
        "clinic": "Sunrise Family Clinic",
        "clinic_addr": "12 MG Road, Bengaluru 560001   |   Ph: 080-2222 1111",
        "provider_name": "Dr. Anil Rao",
        "provider_qual": "MBBS, MD (General Medicine)",
        "provider_specialty": "General Medicine",
        "reg": "Reg. No. KMC 45231",
        "date_display": "10-02-2026",
        "record_date": "2026-02-10",
        "patient_name": "Rahul Mehta",
        "patient_age": 32,
        "patient_sex": "M",
        "diagnosis": "Acute pharyngitis",
        "meds": [
            {"form_prefix": "Tab. ", "name": "Amoxicillin", "strength": "500 mg", "form": "tablet",
             "dose": "1 tablet", "frequency": "twice daily", "duration": "5 days"},
            {"form_prefix": "Tab. ", "name": "Paracetamol", "strength": "650 mg", "form": "tablet",
             "dose": "1 tablet", "frequency": "three times a day", "duration": "3 days"},
        ],
        "advice": "Warm saline gargles. Rest and plenty of fluids.",
        "follow_up": "Review after 5 days if not improving.",
        "expected_confidence": "high",
        "expected_needs_review": "N",
        "scoring_notes": "Clean typed baseline. All fields should extract cleanly.",
    },
    {
        "sample_id": "sample_02_toothpain",
        "kind": "handwritten_rx",
        "document_type": "prescription",
        "clinic": "City Dental Care",
        "provider_name": "Dr. S. Kapoor",
        "provider_qual": "BDS, MDS",
        "provider_specialty": "Dentistry",
        "date_display": "5/3/26",
        "record_date": "2026-03-05",
        "patient_name": "Rahul Mehta",
        "patient_age": 32,
        "patient_sex": "M",
        "dx_written": "Irrev. pulpitis 46",
        "diagnosis": "Irreversible pulpitis (tooth 46)",
        "rx_written": [
            "T. Augmentin 625   1-0-1  x 5d",
            "T. Brufen 400   1-1-1  x 3d",
            "Cap. Pan 40   1-0-0  b/f",
        ],
        "meds": [
            {"form_prefix": "Tab. ", "name": "Augmentin", "strength": "625 mg", "form": "tablet",
             "dose": "1 tablet", "frequency": "twice daily", "duration": "5 days"},
            {"form_prefix": "Tab. ", "name": "Brufen", "strength": "400 mg", "form": "tablet",
             "dose": "1 tablet", "frequency": "three times a day", "duration": "3 days"},
            {"form_prefix": "Cap. ", "name": "Pan", "strength": "40 mg", "form": "capsule",
             "dose": "1 capsule", "frequency": "once daily before food", "duration": None},
        ],
        "advice_written": "Soft diet, avoid cold",
        "advice": "Soft diet, avoid cold.",
        "follow_written": "RCT adv. Review 3d",
        "follow_up": "Root canal treatment advised. Review after 3 days.",
        "expected_confidence": "low",
        "expected_needs_review": "Y",
        "scoring_notes": "Handwritten and terse. Expect low confidence and a needs_review flag. Brand names as written (Augmentin, Brufen, Pan).",
    },
    {
        "sample_id": "sample_03_diabetes_lab",
        "kind": "lab",
        "document_type": "lab_report",
        "clinic": "MedLab Diagnostics",
        "clinic_addr": "24 Residency Road, Bengaluru   |   NABL Accredited",
        "referred_by": "Dr. Anil Rao",
        "report_title": "BIOCHEMISTRY REPORT",
        "date_display": "15-Jan-2026",
        "record_date": "2026-01-15",
        "patient_name": "Rahul Mehta",
        "patient_age": 32,
        "patient_sex": "M",
        "provider_specialty": None,
        "investigations": [
            {"name": "HbA1c", "value": "7.8", "unit": "%", "reference_range": "< 5.7", "printed_flag": "H", "flag": "high"},
            {"name": "Fasting Plasma Glucose", "value": "142", "unit": "mg/dL", "reference_range": "70 - 100", "printed_flag": "H", "flag": "high"},
            {"name": "Total Cholesterol", "value": "185", "unit": "mg/dL", "reference_range": "< 200", "printed_flag": "", "flag": "unknown"},
            {"name": "Serum Creatinine", "value": "0.9", "unit": "mg/dL", "reference_range": "0.7 - 1.3", "printed_flag": "", "flag": "unknown"},
        ],
        "expected_confidence": "high",
        "expected_needs_review": "N",
        "scoring_notes": "Tests the printed-only flag rule. HbA1c and FPG carry a printed 'H' (flag=high). Cholesterol and Creatinine have NO printed flag, so flag MUST be 'unknown', never computed to 'normal'.",
    },
    {
        "sample_id": "sample_04_eye_rash",
        "kind": "typed_rx",
        "document_type": "prescription",
        "clinic": "Skin & Care Dermatology",
        "clinic_addr": "5 Lavelle Road, Bengaluru 560001   |   Ph: 080-4444 3333",
        "provider_name": "Dr. Neha Verma",
        "provider_qual": "MBBS, MD (Dermatology)",
        "provider_specialty": "Dermatology",
        "reg": "Reg. No. KMC 51890",
        "date_display": "02 Apr 2026",
        "record_date": "2026-04-02",
        "patient_name": "Rahul Mehta",
        "patient_age": 32,
        "patient_sex": "M",
        "diagnosis": "Allergic contact dermatitis, left upper eyelid",
        "meds": [
            {"form_prefix": "", "name": "Mometasone Furoate 0.1%", "strength": "Ointment", "form": "ointment",
             "dose": "apply thin layer", "frequency": "once daily", "duration": "7 days"},
            {"form_prefix": "Tab. ", "name": "Levocetirizine", "strength": "5 mg", "form": "tablet",
             "dose": "1 tablet", "frequency": "at night", "duration": "7 days"},
        ],
        "advice": "Avoid rubbing the area. Avoid new cosmetics or soaps near the eye.",
        "follow_up": "Review after 1 week.",
        "expected_confidence": "high",
        "expected_needs_review": "N",
        "scoring_notes": "Topical ointment plus an oral antihistamine. Tests a non-tablet form and a periorbital site.",
    },
]


def build_label(d):
    meds = [
        {"name": m["name"], "strength": m["strength"], "form": m["form"],
         "dose": m["dose"], "frequency": m["frequency"], "duration": m["duration"]}
        for m in d.get("meds", [])
    ]
    invs = [
        {"name": i["name"], "value": i["value"], "unit": i["unit"],
         "reference_range": i["reference_range"], "flag": i["flag"]}
        for i in d.get("investigations", [])
    ]
    return {
        "sample_id": d["sample_id"],
        "image": f'samples/{d["sample_id"]}.png',
        "document_type": d["document_type"],
        "expected": {
            "record_date": d["record_date"],
            "provider": {
                "name": d.get("provider_name"),
                "specialty": d.get("provider_specialty"),
                "clinic": d["clinic"],
            },
            "patient": {"name": d["patient_name"], "age": d["patient_age"], "sex": d["patient_sex"]},
            "diagnosis_stated_text": d.get("diagnosis"),
            "medications": meds,
            "investigations": invs,
            "advice_verbatim": d.get("advice"),
            "follow_up": d.get("follow_up"),
        },
        "expected_confidence": d["expected_confidence"],
        "expected_needs_review": d["expected_needs_review"],
        "scoring_notes": d["scoring_notes"],
    }


def main():
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    os.makedirs(LABELS_DIR, exist_ok=True)
    for d in DOCS:
        img = RENDERERS[d["kind"]](d)
        img_path = os.path.join(SAMPLES_DIR, f'{d["sample_id"]}.png')
        img.save(img_path)
        label = build_label(d)
        label_path = os.path.join(LABELS_DIR, f'{d["sample_id"]}.json')
        with open(label_path, "w", encoding="utf-8") as f:
            json.dump(label, f, indent=2, ensure_ascii=False)
        print(f'wrote {os.path.relpath(img_path, ROOT)}  and  {os.path.relpath(label_path, ROOT)}')


if __name__ == "__main__":
    main()
