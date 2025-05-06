import os
import re
import camelot
import pdfplumber
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import (
    Base, Season, Show, Group, Classification,
    HostLocation, Performance, CaptionScore, CaptionWeight
)

# ─────────────────────────────────────────────────────────────────────────────
# 1) Split a header string like "Percussion Scholastic A – Block 1"
#    into ("Percussion Scholastic A", 1)
# ─────────────────────────────────────────────────────────────────────────────
CLASS_BLOCK_RE = re.compile(
    r"^(?P<class>.*?)(?:\s*[–-]\s*Block\s*(?P<block>\d+))?$",
    re.IGNORECASE
)

def parse_class_block(text: str):
    m = CLASS_BLOCK_RE.match(text.strip())
    if not m:
        raise ValueError(f"Unrecognized class/block: {text!r}")
    classification = m.group("class").strip()
    block = int(m.group("block")) if m.group("block") else None
    return classification, block


# ─────────────────────────────────────────────────────────────────────────────
# 2) Read free text from a PDF page and extract show metadata & class/block
# ─────────────────────────────────────────────────────────────────────────────
DATE_RE     = re.compile(r"([A-Za-z]+ \d{1,2}, \d{4})")
LOC_RE      = re.compile(r"–\s*([A-Za-z ]+,\s*[A-Z]{2})")
SHOWNAME_RE = re.compile(r"^(.+?HS(?: Saturday| Sunday| Finals| Prelims))", re.MULTILINE)
CLASS_RE    = re.compile(r"(Percussion Scholastic [A-Za-z ]+(?: – Block \d+)?)")

def scan_pdf_header(pdf_path: str, page_no: int = 0):
    meta = {}
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[page_no].extract_text() or ""

    # Show name
    m = SHOWNAME_RE.search(text)
    if m:
        meta['show_name'] = m.group(1).strip()

    # Location
    m = LOC_RE.search(text)
    if m:
        meta['location'] = m.group(1).strip()

    # Date
    m = DATE_RE.search(text)
    if m:
        dt = datetime.strptime(m.group(1), "%B %d, %Y").date()
        meta['show_date'] = dt

    # Classification text
    m = CLASS_RE.search(text)
    if m:
        meta['classification_text'] = m.group(1).strip()

    # Block extracted later via parse_class_block()
    return meta


# ─────────────────────────────────────────────────────────────────────────────
# 3) Extract tables with Camelot: lattice first, then stream fallback
# ─────────────────────────────────────────────────────────────────────────────

def extract_tables(pdf_path: str, pages: str = 'all'):
    tables = camelot.read_pdf(pdf_path, pages=pages, flavor='lattice')
    if tables.n == 0:
        tables = camelot.read_pdf(
            pdf_path,
            pages=pages,
            flavor='stream',
            row_tol=10,
            edge_tol=50
        )
    return tables


# ─────────────────────────────────────────────────────────────────────────────
# 4) Clean raw Camelot table: drop multi-row header & build flat columns
# ─────────────────────────────────────────────────────────────────────────────

def clean_table(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()
    # Combine first two rows for header names
    header1 = df.iloc[0].fillna("")
    header2 = df.iloc[1].fillna("")
    cols = [f"{h1} {h2}".strip() for h1, h2 in zip(header1, header2)]
    df = df.drop(index=[0, 1]).reset_index(drop=True)
    df.columns = cols
    # Rename key columns
    df = df.rename(columns={cols[0]: 'Group', cols[1]: 'Home City'})
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 5) Split multi-line cells into numeric columns for each caption
# ─────────────────────────────────────────────────────────────────────────────

def split_caption_cells(df: pd.DataFrame) -> pd.DataFrame:
    def split_cell(cell):
        parts = cell.split("\n")
        comp = float(parts[0])
        perf = float(parts[1])
        total = float(parts[2])
        place = int(parts[3])
        return comp, perf, total, place

    caption_keys = ['Effect - Music', 'Effect - Visual', 'Music', 'Visual', 'SubTotal']
    for key in caption_keys:
        if key in df.columns:
            slug = key.lower().replace(' ', '_').replace('-', '')
            new_cols = [f"{slug}_{f}" for f in ['comp', 'perf', 'total', 'place']]
            df[new_cols] = pd.DataFrame(
                df[key].apply(split_cell).tolist(),
                index=df.index
            )
            df = df.drop(columns=[key])
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 6) ORM helper: get or create an object by kwargs
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create(session: Session, model, defaults=None, **kwargs):
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        return instance
    params = {**kwargs}
    if defaults:
        params.update(defaults)
    instance = model(**params)
    session.add(instance)
    session.flush()
    return instance


# ─────────────────────────────────────────────────────────────────────────────
# 7) Ingest a single PDF: scan header, upsert show, then ingest performances
# ─────────────────────────────────────────────────────────────────────────────

def ingest_pdf(session: Session, pdf_path: str):
    basename = os.path.basename(pdf_path)
    # 7a) Header metadata
    hdr0 = scan_pdf_header(pdf_path, page_no=0)
    show_name = hdr0.get('show_name')
    show_date = hdr0.get('show_date')
    location  = hdr0.get('location')

    # 7b) Upsert Season & Host
    season = get_or_create(session, Season, year=show_date.year)
    host   = get_or_create(session, HostLocation, name=location)

    # 7c) Upsert Show
    show = get_or_create(
        session, Show,
        season_id=season.id,
        name=show_name,
        date=show_date,
        location=location,
        pdf_name=basename
    )

    # If this Show already existed, wipe its old performances so we can re‐write fresh
    if show.performances:
        print(f"[OVERWRITE] Clearing {len(show.performances)} old performances for {show.name} on {show.date}")
        show.performances.clear()
        session.flush()

    # 7d) Load caption weights map
    cw_map = {cw.caption: cw.weight for cw in season.caption_weights}

    # 7e) Open PDF and ingest page-by-page
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, _ in enumerate(pdf.pages):
            hdr = scan_pdf_header(pdf_path, page_no)
            cls_text = hdr.get('classification_text', '')
            classification, block = parse_class_block(cls_text)

            tables = extract_tables(pdf_path, pages=str(page_no+1))
            for table in tables:
                df = clean_table(table.df)
                df = split_caption_cells(df)

                for row in df.to_dict(orient='records'):
                    grp = get_or_create(session, Group,
                                        name=row['Group'],
                                        home_city=row['Home City'])
                    cls = get_or_create(session, Classification,
                                        name=classification)

                    perf = Performance(
                        show_id           = show.id,
                        group_id          = grp.id,
                        classification_id = cls.id,
                        block_number      = block,
                        total_score       = row.get('subtotal_total', 0.0),
                        placement         = row.get('subtotal_place', 0),
                        penalty           = row.get('timing_penalty', 0.0)
                    )
                    session.add(perf)
                    session.flush()

                    # Caption scores
                    for cap in ['Effect - Music', 'Effect - Visual', 'Music', 'Visual']:
                        slug = cap.lower().replace(' ', '_').replace('-', '')
                        cs = CaptionScore(
                            performance=perf,
                            caption=cap,
                            weight=cw_map.get(cap, 0.0),
                            comp_score=row.get(f"{slug}_comp", 0.0),
                            perf_score=row.get(f"{slug}_perf", 0.0),
                            placement=row.get(f"{slug}_place", 0),
                            judge_id=None
                        )
                        session.add(cs)

    session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 8) Main: loop over a folder of PDFs
# ─────────────────────────────────────────────────────────────────────────────

def main(pdf_folder: str, db_url: str = 'sqlite:///scoresdb.sqlite'):
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    for fn in sorted(os.listdir(pdf_folder)):
        if not fn.lower().endswith('.pdf'):
            continue
        path = os.path.join(pdf_folder, fn)
        try:
            ingest_pdf(session, path)
        except Exception as e:
            session.rollback()
            print(f"[ERROR] {fn}: {e}")

    session.close()


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print("Usage: python parser.py /path/to/pdf_folder")
        sys.exit(1)
    main(sys.argv[1])
