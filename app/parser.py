#!/usr/bin/env python3
import os
import re
import sys
import camelot
import pdfplumber
import pandas as pd
from datetime import datetime, date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import (
    Base,
    Season,
    Show,
    Group,
    Classification,
    HostLocation,
    Performance,
    CaptionScore,
    CaptionWeight,
)

# ─────────────────────────────────────────────────────────────────────────────
# 1) CLASS/BLOCK SPLIT (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
CLASS_BLOCK_RE = re.compile(r"^(?P<class>.*?)(?:\s*[–-]\s*Block\s*(?P<block>\d+))?$",
                            re.IGNORECASE)

def parse_class_block(text: str) -> tuple[str, int|None]:
    m = CLASS_BLOCK_RE.match((text or "").strip())
    if not m:
        raise ValueError(f"Unrecognized class/block: {text!r}")
    classification = m.group("class").strip()
    block = int(m.group("block")) if m.group("block") else None
    return classification, block

# ─────────────────────────────────────────────────────────────────────────────
# 2) HEADER SCAN (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
DATE_RE     = re.compile(r"([A-Za-z]+ \d{1,2},\s*\d{4})")
LOC_RE      = re.compile(r"–\s*([A-Za-z ]+,\s*[A-Z]{2})")
SHOWNAME_RE = re.compile(r"([A-Za-z ]+ HS(?: Saturday| Sunday| Finals| Prelims))")
CLASS_RE    = re.compile(r"(Percussion Scholastic [A-Za-z ]+(?: – Block \d+)?)")

def scan_pdf_header(pdf_path: str, page_no: int = 0) -> dict:
    meta = {}
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[page_no].extract_text() or ""

    # name
    m = SHOWNAME_RE.search(text)
    if m: meta["show_name"] = m.group(1).strip()

    # location (header)
    m = LOC_RE.search(text)
    if m: meta["location"] = m.group(1).strip()

    # date
    m = DATE_RE.search(text)
    if m: meta["show_date"] = datetime.strptime(m.group(1), "%B %d, %Y").date()

    # raw class text
    m = CLASS_RE.search(text)
    if m: meta["classification_text"] = m.group(1).strip()

    return meta

# ─────────────────────────────────────────────────────────────────────────────
# 3) FILENAME FALLBACK
#    Pattern: YYYY_MM_DD_hostid[_HS]_<day>_<city_slug>_<state>
# ─────────────────────────────────────────────────────────────────────────────
WEEKDAYS = {"saturday","sunday","prelims","semifinals","finals"}

def parse_filename(fn: str) -> dict:
    """
    Returns:
      {
        'show_date': date(...),
        'hostid':     str,         # e.g. "temescal_canyon"
        'weekday':    str,         # e.g. "Saturday"
        'city':       str,         # e.g. "Lake Elsinore"
        'state':      str,         # e.g. "CA"
        'show_name':  str,         # e.g. "Temescal Canyon HS Saturday"
      }
    """
    base = os.path.splitext(fn)[0]
    parts = base.split("_")
    if len(parts) < 6:
        raise ValueError(f"Filename too short: {fn!r}")

    # date
    y, mo, da = parts[0], parts[1], parts[2]
    show_date = date(int(y), int(mo), int(da))

    # find the weekday token index
    wd_idx = next((i for i,p in enumerate(parts) if p.lower() in WEEKDAYS), None)
    if wd_idx is None:
        raise ValueError(f"No weekday in filename: {fn!r}")

    # hostid is parts[3:wd_idx], possibly ending with 'hs'
    hostid_parts = parts[3:wd_idx]
    # if last is 'hs', keep it
    if hostid_parts and hostid_parts[-1].lower() == "hs":
        hostid = "_".join(hostid_parts)
    else:
        hostid = "_".join(hostid_parts + ["hs"])  # ensure HS

    weekday = parts[wd_idx].title()

    # city slug is everything between weekday and last part
    city_parts = parts[wd_idx+1:-1]
    city = " ".join(p.title() for p in city_parts)

    state = parts[-1].upper()

    show_name = f"{hostid.replace('_',' ').title()} {weekday}"

    return {
        "show_date": show_date,
        "hostid":    hostid,
        "weekday":   weekday,
        "city":      city,
        "state":     state,
        "show_name": show_name,
    }

# ─────────────────────────────────────────────────────────────────────────────
# 4) TABLE EXTRACT & CLEAN (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def extract_tables(pdf_path: str, pages: str='all'):
    tables = camelot.read_pdf(pdf_path, pages=pages, flavor='lattice')
    if tables.n == 0:
        tables = camelot.read_pdf(
            pdf_path, pages=pages,
            flavor='stream', row_tol=10, edge_tol=50
        )
    return tables

def clean_table(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    - Merge the top two header rows into flat column names
    - Deduplicate those names (appending _1, _2, …)
    - Drop the header rows, reset the index
    - Rename the first two columns to "Group" and "Home City"
    """
    # 1) build the raw merged headers
    header1 = raw_df.iloc[0].fillna("").astype(str)
    header2 = raw_df.iloc[1].fillna("").astype(str)
    merged = [(h1 + " " + h2).strip() for h1,h2 in zip(header1, header2)]

    # 2) dedupe the merged names
    counts = {}
    unique_cols = []
    for name in merged:
        if not name:
            name = "BLANK"
        if name in counts:
            counts[name] += 1
            unique_cols.append(f"{name}_{counts[name]}")
        else:
            counts[name] = 0
            unique_cols.append(name)

    # 3) drop the two header rows and reassign
    df = raw_df.drop(index=[0,1]).reset_index(drop=True)
    df.columns = unique_cols

    # 4) ensure the first two columns are named correctly
    #    even if the merged headers were blank or duplicated
    cols = list(df.columns)
    cols[0] = "Group"
    cols[1] = "Home City"
    df.columns = cols

    return df


def split_caption_cells(df: pd.DataFrame) -> pd.DataFrame:
    def split_cell(c):
        p = (c or "").split("\n")
        return float(p[0]), float(p[1]), float(p[2]), int(p[3])
    for cap in ["Effect - Music","Effect - Visual","Music","Visual","SubTotal"]:
        if cap not in df.columns: continue
        slug = cap.lower().replace(" ","_").replace("-","")
        vals = [split_cell(c) for c in df[cap]]
        df[f"{slug}_comp"], df[f"{slug}_perf"], df[f"{slug}_total"], df[f"{slug}_place"] = zip(*vals)
        df = df.drop(columns=[cap])
    return df

# ─────────────────────────────────────────────────────────────────────────────
# 5) ORM get_or_create (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def get_or_create(session: Session, model, **kw):
    obj = session.query(model).filter_by(**kw).first()
    if obj: return obj
    obj = model(**kw)
    session.add(obj)
    session.flush()
    return obj

# ─────────────────────────────────────────────────────────────────────────────
# 6) Ingest one PDF
# ─────────────────────────────────────────────────────────────────────────────
def ingest_pdf(session: Session, pdf_path: str):
    fn = os.path.basename(pdf_path)

    # header scan
    hdr = scan_pdf_header(pdf_path, 0)
    show_name = hdr.get("show_name")
    show_date = hdr.get("show_date")

    # fallback if header missed name/date
    if not show_name or not show_date:
        fb = parse_filename(fn)
        show_name = show_name or fb["show_name"]
        show_date = show_date or fb["show_date"]
        hostid    = fb["hostid"]
        city      = fb["city"]
        state     = fb["state"]
    else:
        # hostid from show_name
        hostid = show_name.rsplit(" ",1)[0].lower().replace(" ","_")
        # city/state from header location
        loc = hdr.get("location","")
        if "," in loc:
            city, state = [x.strip() for x in loc.split(",",1)]
        else:
            city = state = None

    # season
    season = get_or_create(session, Season, year=show_date.year)

    # host upsert using the hostid as name
    host = get_or_create(
        session, HostLocation,
        name = hostid.replace("_"," ").title(),
        city = city,
        state= state
    )
    # 8e) Compute week number: count of earlier shows in this season + 1
    week_num = session.query(Show) \
        .filter(Show.season_id == season.id, Show.date < show_date) \
        .count() + 1

    # 8f) Upsert Show *by pdf_name only*, updating the other fields if it already exists
    show = session.query(Show) \
                  .filter_by(pdf_name=fn) \
                  .one_or_none()

    if show:
        # Update in case anything changed
        show.name      = show_name
        show.date      = show_date
        show.season_id = season.id
        show.host_id   = host.id
        show.week      = week_num
        # no need to set pdf_name—it’s the same
    else:
        show = Show(
            season_id = season.id,
            name      = show_name,
            date      = show_date,
            host_id   = host.id,
            week      = week_num,
            pdf_name  = fn
        )
        session.add(show)

    session.flush()

    # overwrite existing
    if show.performances:
        show.performances.clear()
        session.flush()

    # caption weights map
    cw_map = {cw.caption:cw.weight for cw in season.caption_weights}

    # ingest pages
    with pdfplumber.open(pdf_path) as pdf:
        for pno in range(len(pdf.pages)):
            ph = scan_pdf_header(pdf_path, pno)
            cls_txt = ph.get("classification_text","")
            classification, block = parse_class_block(cls_txt)

            tables = extract_tables(pdf_path, pages=str(pno+1))
            for tbl in tables:
                df = clean_table(tbl.df)
                df = split_caption_cells(df)

                for row in df.to_dict("records"):
                    grp = get_or_create(
                        session, Group,
                        name     = row["Group"],
                        home_city=row["Home City"]
                    )
                    cls = get_or_create(
                        session, Classification,
                        name=classification
                    )

                    perf = Performance(
                        show_id           = show.id,
                        group_id          = grp.id,
                        classification_id = cls.id,
                        block_number      = block,
                        total_score       = row.get("subtotal_total",0.0),
                        placement         = row.get("subtotal_place",0),
                        penalty           = row.get("timing_penalty",0.0)
                    )
                    session.add(perf)
                    session.flush()

                    for cap in ["Effect - Music","Effect - Visual","Music","Visual"]:
                        slug = cap.lower().replace(" ","_").replace("-","")
                        cs = CaptionScore(
                            performance=perf,
                            caption    = cap,
                            weight     = cw_map.get(cap,0.0),
                            comp_score = row.get(f"{slug}_comp",0.0),
                            perf_score = row.get(f"{slug}_perf",0.0),
                            placement  = row.get(f"{slug}_place",0),
                            judge_id   = None
                        )
                        session.add(cs)

    session.commit()

# ─────────────────────────────────────────────────────────────────────────────
# 7) Main: batch ingest folder
# ─────────────────────────────────────────────────────────────────────────────
def main(folder: str, db_url: str="sqlite:///scoresdb.sqlite"):
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()

    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".pdf"):
            continue
        path = os.path.join(folder, fn)
        try:
            ingest_pdf(sess, path)
        except Exception as e:
            sess.rollback()
            print(f"[ERROR] {fn}: {e}")

    sess.close()

if __name__=="__main__":
    if len(sys.argv)!=2:
        print("Usage: python3 parser.py /path/to/pdf_folder")
        sys.exit(1)
    main(sys.argv[1])
