import camelot
import sys

def extract_tables(pdf_path):
    tables = camelot.read_pdf(pdf_path, pages='all', flavor='lattice')
    if tables.n == 0:
        tables = camelot.read_pdf(
            pdf_path,
            pages='all',
            flavor='stream',
            row_tol=10,
            edge_tol=50,
        )
    return tables

if __name__ == "__main__":
    pdf = sys.argv[1]
    tables = extract_tables(pdf)
    print(f"Found {tables.n} tables in {pdf}\n")

    for i, table in enumerate(tables):
        print(f"--- Table {i} ({table.shape[0]} rows × {table.shape[1]} cols) ---")
        # print full DF, no truncation
        print(table.df.to_string(index=False))
        print()

