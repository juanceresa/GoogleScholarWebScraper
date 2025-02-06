import requests
from bs4 import BeautifulSoup
import os
import pandas as pd


USERNAME = os.getenv("OXYLABS_USERNAME")
PASSWORD = os.getenv("OXYLABS_PASSWORD")

file_path = "investigadores_depurados_con_gs_man-checks.xlsx"
df = pd.read_excel(file_path)
scholar_column = "GS"
missing_profiles = []  # Store missing profiles

# Function to search Google Scholar for profile
def search_google_scholar(name_query):
    """Search Google Scholar for a given name and return a profile URL (if found)."""
    # Search replaces ' ' with '+' in format of Google Scholar
    search_url = f"https://scholar.google.com/scholar?q={name_query.replace(' ', '+')}"
    payload = {
        "source": "google",
        "url": search_url,
        "parse": True,
    }

    response = requests.post("https://realtime.oxylabs.io/v1/queries",
                             auth=(USERNAME, PASSWORD),
                             json=payload)

    if response.status_code == 200:
        data = response.json()
        results = data.get("data", {}).get("results", [])

        for result in results:
            if "User profiles for" in result.get("title", "") and "scholar.google.com/citations?" in result.get("link", ""):
                return result["link"]  # Return the profile link
            if "scholar.google.com/citations?" in result.get("link", ""):
                return result["link"]  # Return first profile found

    print(f"No profile found for {name_query}")
    return None

# Function to search for DOIs using CrossRef API
def search_doi(name_query):
    """Search for DOIs using the author's name in CrossRef API."""
    crossref_url = f"https://api.crossref.org/works?query.author={name_query.replace(' ', '+')}"

    response = requests.get(crossref_url)

    if response.status_code == 200:
        data = response.json()
        items = data.get("message", {}).get("items", [])
        if items:
            return [item.get("DOI") for item in items[:3]]  # Return first 3 DOIs

    return None  # No DOI found



# Process rows where Google Scholar URL is missing
for index, row in df.iterrows():
    if pd.isna(row[scholar_column]):  # If the Google Scholar URL is missing
        name_query = row["Nombre y apellidos"]
        scholar_link = search_google_scholar(name_query)

        if scholar_link:
            df.at[index, scholar_column] = scholar_link  # Update DataFrame with found URL
        else:
            missing_profiles.append(name_query)  # Log missing profile

             # Try finding DOIs as an alternative
            doi_list = search_doi(name_query)
            if doi_list:
                df.at[index, "DOI"] = ", ".join(doi_list)  # Save DOIs if found
            else:
                df.at[index, "DOI"] = "None"

with open("missing_profiles.txt", "w") as f:
    for name in missing_profiles:
        f.write(name + "\n")

# Save the updated DataFrame back to Excel
df.to_excel("updated_file.xlsx", index=False)

print("Scraping complete. Results saved to 'updated_file.xlsx'.")