import os
import pandas as pd
from dotenv import load_dotenv
import certifi
import urllib3
import json
import time
import requests
from search import search_google_scholar, search_doi, token_match_score, search_doi_loose


# Load environment variables
load_dotenv()
urllib3.disable_warnings()

# Web Scraper API Credentials (For structured data)
scraper_user = os.getenv("OXYLABS_USERNAME")
scraper_pass = os.getenv("OXYLABS_PASSWORD")

# Web Unblocker API Credentials (For Google Scholar)
unblock_user = os.getenv("WEB_UNBLOCK_USERNAME")
unblock_pass = os.getenv("WEB_UNBLOCK_PASSWORD")

# Web Unblocker Proxy URL
unblock_proxy = f"http://{unblock_user}:{unblock_pass}@unblock.oxylabs.io:60000"

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

# Process rows where Google Scholar URL is missing and/or DOIs need fetching
save_interval = 10  # Save every 10 rows
rows_processed = 0

for index, row in df.iterrows():
    # Skip rows if GS or DOI are already present
    if not pd.isna(row[scholar_column]) or not pd.isna(row["DOI"]):
        continue

    name_query = row["Nombre y apellidos"]
    scholarship_year = row["Año beca"]
    institution_name = str(row["Trabajo.institucion"]) if not pd.isna(row["Trabajo.institucion"]) else ""

    # Step 1: Search for Google Scholar profile
    scholar_link = search_google_scholar(name_query, scraper_api_url, scraper_user, scraper_pass, unblock_proxy)

    if scholar_link:
        df.at[index, scholar_column] = scholar_link
        print(f"Found GS Profile for {name_query}: {scholar_link}")
        continue  # Skip DOI search if GS link is found

    # Step 2: Search for DOIs (prioritizing correct institution & date range)
    doi_results = search_doi(name_query, scholarship_year, institution_name)

    if doi_results and len(doi_results) > 0:
        # Separate out the OK results:
        p_only = [d for d in doi_results if d["status"] == "PAREJA"]
        ni_only = [d for d in doi_results if d["status"] == "nombre+institucion"]

        # Match found
        if p_only:
            final_results = p_only
            df.at[index, "DOI_Status"] = f"PAREJA"
        # No matches found, so keep nombre+institucion
        elif ni_only:
            final_results = ni_only
            df["DOI_Status"] = df["DOI_Status"].astype(str)
            df.at[index, "DOI_Status"] = f"nombre+institucion"
        # Return base case
        else:
            final_results = doi_results
            df.at[index, "DOI_Status"] = f"REVISA"

        # 'DOI' field from final_results
        doi_formatted = ", ".join([res["doi"] for res in final_results])
        df.at[index, "DOI"] = doi_formatted

        print(f"✅ Found DOIs for {name_query}: {doi_formatted}")
        print(f"DOI Status for {name_query}: {df.at[index, 'DOI_Status']}")


    else:
        df.at[index, "DOI"] = None
        df.at[index, "DOI_Status"] = None
        print(f"No DOIs found for {name_query}, preforming loose search")

        loose_doi_results = search_doi_loose(name_query)
        doi_formatted = ", ".join([res["doi"] for res in loose_doi_results])
        df.at[index, "DOI"] = doi_formatted
        df.at[index, "DOI_Status"] = "loose search"

    rows_processed += 1

    # Save every 10 rows
    if rows_processed % save_interval == 0:
        df.to_excel(file_path, index=False)
        print(f"📂 Progress saved at row {index}.")

    # Small delay to avoid rate limits
    time.sleep(2)

# Final save
df.to_excel(file_path, index=False)
print("🎉 Scraping complete. All results saved to Excel.")
