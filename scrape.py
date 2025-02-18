import os
import pandas as pd
from dotenv import load_dotenv
import certifi
import urllib3
import json
import time
import requests

from search import search_google_scholar, search_doi  # or whichever functions you need

# Load environment variables
load_dotenv()
urllib3.disable_warnings()

# Web Scraper API Credentials (For structured data)
scraper_user = os.getenv("OXYLABS_USERNAME")
scraper_pass = os.getenv("OXYLABS_PASSWORD")

# Web Unblocker API Credentials (For Google Scholar)
unblock_user = os.getenv("WEB_UNBLOCK_USERNAME")
unblock_pass = os.getenv("WEB_UNBLOCK_PASSWORD")

proxies = {
  'http': f'http://{unblock_user}:{unblock_pass}@unblock.oxylabs.io:60000',
  'https': f'https://{unblock_user}:{unblock_pass}@unblock.oxylabs.io:60000',
}


# Web Scraper API URL
scraper_api_url = "https://realtime.oxylabs.io/v1/queries"

# File path and DataFrame setup
file_path = "investigadores_depurados_con_gs_man-checks.xlsx"
df = pd.read_excel(file_path)
scholar_column = "GS"

# Ensure required columns exist
for col in ["DOI", "DOI_Status"]:
    if col not in df.columns:
        df[col] = None

save_interval = 10  # Save every 10 rows
rows_processed = 0

for index, row in df.iterrows():
    # Skip rows if GS or DOI are already present
    if not pd.isna(row[scholar_column]) or not pd.isna(row["DOI"]):
        continue

    name_query = str(row["Nombre y apellidos"])
    scholarship_year = int(row["Año beca"]) if not pd.isna(row["Año beca"]) else 0
    institution_name = str(row["Trabajo.institucion"]) if not pd.isna(row["Trabajo.institucion"]) else ""

    # Step 1: Search for Google Scholar profile
    scholar_link = search_google_scholar(
        name_query,
        unblock_user,
        unblock_pass,
    )

    if scholar_link:
        df.at[index, scholar_column] = scholar_link
        print(f"Found GS Profile for {name_query}: {scholar_link}")
        # Optionally also do a DOI search here, or skip
        continue  # If you skip, no DOIs are fetched

    # Suppose your Excel has columns "Nombre", "Apellido1", "Apellido2"
    given_name = str(row["Nombre"]).strip() if not pd.isna(row["Nombre"]) else ""
    # Note: The search_doi function currently uses name_query and given_name.
    final_results = search_doi(name_query, given_name, scholarship_year, institution_name, debug=False)

    if final_results:
        # If search_doi returns a flat list with one dictionary, take the first one.
        result = final_results[0]
        doi_formatted = result["doi"]
        df.at[index, "DOI"] = doi_formatted
        df.at[index, "DOI_Status"] = str(result["score"])  # Use "score" not "status"
        print(f"✅ Found DOIs for {name_query}: {doi_formatted}")
        print(f"DOI Status for {name_query}: {df.at[index,'DOI_Status']}")
    else:
        print(f"No DOIs found for {name_query}.")
        df.at[index, "DOI"] = None
        df.at[index, "DOI_Status"] = None


    rows_processed += 1
    if rows_processed % save_interval == 0:
        df.to_excel(file_path, index=False)
        print(f"📂 Progress saved at row {index}.")

    time.sleep(2)

# Final save
df.to_excel(file_path, index=False)
print("🎉 Scraping complete. All results saved to Excel.")