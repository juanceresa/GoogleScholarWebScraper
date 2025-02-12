import re
import requests
from crossref.restful import Works

def name_variations(full_name):
    """
    Given a full name string like 'Rebeca Acin Perez',
    produce a list of possible variations, e.g.:
      - 'rebeca acin perez'
      - 'acin perez'
      - 'acin-perez'
      - 'perez'
    Removes duplicates by converting to a set, then back to list.
    """
    parts = full_name.lower().split()
    # Variation 1: full name
    variations = [" ".join(parts)]

    # Variation 2: just last names
    if len(parts) >= 2:
        last_names = parts[1:]
        # e.g. "acin perez"
        variations.append(" ".join(last_names))

        # e.g. "acin-perez"
        if len(last_names) > 1:
            variations.append("-".join(last_names))

        # e.g. "perez"
        if len(last_names) > 1:
            variations.append(last_names[-1])

    # Make them unique
    return list(set(variations))

def matched_name_in_author(author, full_name):
    """
    Returns True if *any* variation of 'full_name' is fully matched in the
    author's name fields. We:
      1) generate name_variations(full_name)
      2) gather author_name from 'given', 'family', or 'literal'
      3) for each variation, split into tokens and check if *all* appear
         in author_name

    This is stricter than a naive substring check, but still uses your
    existing permutations for partial last names, hyphens, etc.
    """
    # Prepare the author's combined name fields
    given = (author.get('given')   or '').lower()
    fam   = (author.get('family')  or '').lower()
    lit   = (author.get('literal') or '').lower()
    author_name = (given + " " + fam + " " + lit).strip()

    # Generate all permutations from the input name
    possible_forms = name_variations(full_name)

    # For each variation, we require *all tokens* to be present
    # in the author's name fields:
    for variation in possible_forms:
        form_tokens = variation.split()
        # e.g. 'rebeca acin perez' -> ['rebeca','acin','perez']
        if all(token in author_name for token in form_tokens):
            return True

    return False


def get_publication_year(item):
    """
    Extract publication year from different CrossRef fields (issued, published-print, published-online).
    Returns None if not found.
    """
    for field in ["issued", "published-print", "published-online"]:
        data = item.get(field, {})
        if "date-parts" in data and data["date-parts"]:
            return data["date-parts"][0][0]  # e.g., [[2020, 7, 15]]
    return None

# Function to search Google Scholar for a profile (Using Web Unblocker)
def search_google_scholar(name_query, scraper_api_url, scraper_user, scraper_pass, unblock_proxy):
    """
    Search Google Scholar for a given name and return a profile URL (if found).
    This version tries to handle the 'User profiles for X' result more reliably.
    """
    search_url = f"https://scholar.google.com/scholar?q={name_query.replace(' ', '+')}"
    payload = {
        "source": "google",
        "url": search_url,
        "parse": True,
    }

    try:
        # Send request using Web Unblocker + Oxylabs Real-Time Crawler
        response = requests.post(
            scraper_api_url,  # e.g. "https://realtime.oxylabs.io/v1/queries"
            auth=(scraper_user, scraper_pass),
            json=payload,
            proxies={"http": unblock_proxy, "https": unblock_proxy},
            verify=False,  # per Oxylabs' docs
            timeout=20
        )

        if response.status_code == 200:
            data = response.json()
            results = data.get("data", {}).get("results", [])

            for result in results:
                title = result.get("title", "")
                link = result.get("link", "")

                # 1) Check explicitly for the "User profiles for X" pattern
                if "User profiles for" in title.lower():
                    # Sometimes the direct link is not in result["link"] but in nested fields
                    # so we check all known link fields:
                    possible_links = set()

                    # Main link
                    if link:
                        possible_links.add(link)

                    # Check if there's a list of sub-links, inline links, etc.
                    # (The structure may differ depending on Oxylabs parse format)
                    # For example:
                    inline_links = result.get("inlineLinks", [])
                    for item in inline_links:
                        sub_link = item.get("link", "")
                        if sub_link:
                            possible_links.add(sub_link)

                    related_urls = result.get("relatedUrls", [])
                    for item in related_urls:
                        sub_link = item.get("link", "")
                        if sub_link:
                            possible_links.add(sub_link)

                    # Now see if any link looks like a Google Scholar citations profile
                    for plink in possible_links:
                        if "scholar.google.com/citations?" in plink:
                            return plink  # Return first user-profile link found

                # 2) Fallback: if the link itself has the scholar citations pattern
                if "scholar.google.com/citations?" in link:
                    return link

    except Exception as e:
        print(f"Error fetching Google Scholar profile for {name_query}: {e}")

    print(f"No profile found for {name_query}")
    return None

def search_doi(name_query, scholarship_year, institution_name):
    """
    1. Queries CrossRef by author's name, sorted by 'score' desc.
    2. Finds up to 3 DOIs within ±5 years of scholarship_year with an author whose
       full name contains name_query, and if the institution matches, marks status='OK'.
       If the institution doesn't match, 'status'='additional checking required'.
    3. If no such DOIs found, we return top 3 'fallback' DOIs (label them 'base case (most cited articles)').
    Returns a list of up to 3 dicts with structure: {"doi": ..., "status": ...}.
    """
    crossref_url = (
        "https://api.crossref.org/works"
        f"?query.author={name_query.replace(' ', '+')}"
        "&sort=score&order=desc"
    )

    valid_dois = []
    fallback_dois = []

    try:
        response = requests.get(crossref_url, timeout=20)
        if response.status_code == 200:
            items = response.json().get("message", {}).get("items", [])

            for item in items:
                pub_year = get_publication_year(item)
                doi = item.get("DOI")
                authors = item.get("author", [])
                publisher_str = item.get("publisher", "").lower()
                if not pub_year or not doi or not authors:
                    continue

                # Check name & institution
                matched_name, matched_institution = False, False

                for author in authors:
                    if matched_name_in_author(author, name_query):
                        matched_name = True
                        aff_list = author.get("affiliation", [])
                        # If any affiliation matches institution_name
                        for aff in aff_list:
                            if institution_name.lower() in aff.get("name", "").lower():
                                matched_institution = True
                                break
                        # 2) If still not matched, fallback to checking publisher
                        if not matched_institution:
                            if institution_name.lower() in publisher_str:
                                matched_institution = True

                # Format DOI
                doi_link = f"https://doi.org/{doi}"

                # If within ±5 years
                if matched_name and matched_institution:
                    if (scholarship_year - 5 <= pub_year <= scholarship_year + 5):
                        valid_dois.append({"doi": doi_link, "status": "PAREJA"})
                    else:
                        # Matched name and institution but not scholarship year
                        valid_dois.append({"doi": doi_link, "status": "nombre+institucion"})

                # Always track fallback if name matched, up to 3
                if matched_name and len(fallback_dois) < 3:
                    fallback_dois.append({"doi": doi_link, "status": "REVISA"})

                # Stop after collecting 3 valid DOIs
                if len(valid_dois) >= 3:
                    break
    except Exception as e:
        print(f"Error fetching DOI for {name_query}: {e}")

    # Return valid_dois if we have them, else fallback
    if valid_dois:
        return valid_dois[:3]
    else:
        return fallback_dois[:3]

# ---------------------------------------------------------------------------
#                 Helper for partial matching in search_doi_loose
# ---------------------------------------------------------------------------
def token_match_score(name_tokens, author_obj):
    """
    Returns how many tokens from 'name_tokens' appear in this author's
    'given' + 'family' name.
    """
    given = author_obj.get('given', '').lower()
    family = author_obj.get('family', '').lower()

    match_count = 0
    for token in name_tokens:
        if token in given or token in family:
            match_count += 1
    return match_count


def search_doi_loose(name_query):
    """
    A 'loose' CrossRef search that:
      1. Splits the name into tokens.
      2. Gets CrossRef items, sorts them by (match_score, crossref_score).
      3. Returns up to the top 3 DOIs, each as {"doi": "...", "status": "loose search"}.
    """
    # Clean & tokenize
    query_clean = re.sub(r'[^\w\s]', '', name_query.lower())
    name_tokens = query_clean.split()

    crossref_url = (
        "https://api.crossref.org/works"
        f"?query.author={name_query.replace(' ', '+')}"
        "&sort=score&order=desc"
    )

    try:
        response = requests.get(crossref_url, timeout=20)
        if response.status_code != 200:
            print(f"Error {response.status_code} from CrossRef: {response.text}")
            return []

        items = response.json().get("message", {}).get("items", [])
        results_scored = []

        for it in items:
            doi_id = it.get("DOI", "")
            authors = it.get("author", [])
            best_score = 0

            for au in authors:
                score = token_match_score(name_tokens, au)
                if score > best_score:
                    best_score = score

            crossref_score = it.get("score", 0)
            results_scored.append({
                "doi_id": doi_id,
                "match_score": best_score,
                "crossref_score": crossref_score
            })

        # Sort by match_score desc, then crossref_score desc
        results_scored.sort(
            key=lambda x: (x["match_score"], x["crossref_score"]),
            reverse=True
        )

        # Return top 3 with consistent format: {"doi": "...", "status": "loose search"}
        final_results = []
        for entry in results_scored:
            if len(final_results) >= 3:
                break
            doi_id = entry["doi_id"]
            if doi_id:
                final_results.append({
                    "doi": f"https://doi.org/{doi_id}",
                    "status": "loose search"
                })

        return final_results

    except Exception as e:
        print(f"Error fetching LOOSER DOIs for {name_query}: {e}")
        return []
