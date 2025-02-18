import re
import unidecode
from crossref.restful import Works, Etiquette
import requests
from bs4 import BeautifulSoup
import base64
import time

COMMON_INSTITUTION_WORDS = {
    "universidad", "university", "college", "institute", "instituto",
    "institut", "facultad", "escuela", "politecnica", "autonoma", "superior",
    "council"
}

def parse_spanish_name(full_name):
    """
    Splits 'full_name' into (first_names, paternal_last, maternal_last).
    For example, "Cándida Acín Sáiz" becomes:
         first_names = ["cándida"]
         paternal_last = "acín"
         maternal_last = "sáiz"
    If there is only one last name, maternal_last will be None.
    """
    tokens = full_name.lower().split()
    if len(tokens) < 2:
        return tokens, None, None
    first_names = [tokens[0]]
    if len(tokens) == 2:
        return first_names, tokens[1], None
    else:
        return first_names, tokens[-2], tokens[-1]

def spanish_name_match_combined(author, user_full_name, debug=False):
    """
    Matches Spanish names using a "combined last name" approach.
    It requires that:
      - The user's primary first name appears in the author's 'given' field.
      - At least one token from the combined last name appears in the author's 'family' field.
    (This function is not used in the new strict scoring.)
    """
    user_first, paternal, maternal = parse_spanish_name(user_full_name)
    if debug:
        print(f"DEBUG: Parsed user name: first={user_first}, paternal={paternal}, maternal={maternal}")
    if not paternal:
        return False

    combined_last = paternal
    if maternal:
        combined_last += " " + maternal
    combined_last_norm = unidecode.unidecode(combined_last.lower()).replace('-', ' ')
    combined_tokens = combined_last_norm.split()

    given  = unidecode.unidecode((author.get('given') or '').lower())
    family = unidecode.unidecode((author.get('family') or '').lower()).replace('-', ' ')

    if debug:
        print(f"DEBUG: Checking author => given='{given}', family='{family}'")
        if user_first:
            print(f"DEBUG: User primary first: {user_first[0]}")
        print(f"DEBUG: Combined last tokens: {combined_tokens}")

    if user_first:
        if user_first[0] not in given:
            if debug:
                print(f"DEBUG: FAIL => first token '{user_first[0]}' not in '{given}'")
            return False

    match_count = sum(1 for token in combined_tokens if token in family)
    if debug:
        print(f"DEBUG: Found {match_count} of {len(combined_tokens)} last name tokens in '{family}'")
    if match_count < 1:
        if debug:
            print(f"DEBUG: FAIL => none of the tokens {combined_tokens} found in '{family}'")
        return False

    if debug:
        print("DEBUG: SUCCESS: name matched")
    return True

def tokenize_name_fields(*fields):
    """
    Combines multiple name strings (e.g., 'given' and 'family'), normalizes them,
    replaces hyphens with spaces, and returns a list of tokens.
    Example:
      tokenize_name_fields("Rebeca", "acin-perez") -> ["rebeca", "acin", "perez"]
    """
    combined = " ".join(fields).lower()
    combined = unidecode.unidecode(combined)
    combined = combined.replace("-", " ")
    combined = re.sub(r"[^\w\s]", "", combined)
    tokens = combined.split()
    return tokens

def name_tokens_exact_match(author, user_full_name, debug=False):
    """
    Checks if combining author['given'] and author['family'] (after tokenizing)
    matches exactly the tokens from 'user_full_name'.
    E.g.:
      user_full_name: "rebeca acin perez"
      author: { "given": "Rebeca", "family": "acin-perez" }
      Both become set(["rebeca", "acin", "perez"]) and match exactly.
    """
    author_tokens = tokenize_name_fields(author.get("given", ""), author.get("family", ""))
    user_tokens   = tokenize_name_fields(user_full_name)
    if debug:
        print(f"DEBUG: Exact token match => author tokens: {author_tokens}, user tokens: {user_tokens}")
    if set(author_tokens) == set(user_tokens):
        if debug:
            print("DEBUG: EXACT MATCH => success")
        return True
    if debug:
        print("DEBUG: EXACT MATCH => fail")
    return False

def any_author_matches_name(item, full_name, debug=False):
    authors = item.get("author", [])
    for au in authors:
        if spanish_name_match_combined(au, full_name, debug=debug):
            return True
    return False

def normalize_institution_name(name):
    """
    Normalizes an institution name by lowercasing, removing punctuation/accents,
    and dropping common filler words. Returns a list of tokens.
    """
    name = name.lower()
    name = re.sub(r"[-]", " ", name)
    name = re.sub(r"[^\w\s]", "", name)
    name = unidecode.unidecode(name)
    tokens = name.split()
    filtered = [t for t in tokens if t not in COMMON_INSTITUTION_WORDS]
    return filtered

def institution_match(institution, aff_str):
    """
    Returns True if any token from 'institution' (after normalization)
    appears in 'aff_str' (after normalization).
    """
    inst_tokens = normalize_institution_name(institution)
    aff_tokens  = normalize_institution_name(aff_str)
    if not inst_tokens:
        return False
    return any(t in aff_tokens for t in inst_tokens)

def check_affiliation_or_publisher(item, institution_name):
    """
    Returns True if any author affiliation or the publisher field contains
    any token from institution_name.
    """
    authors = item.get("author", [])
    for au in authors:
        for aff in au.get("affiliation", []):
            aff_name = aff.get("name", "")
            if institution_match(institution_name, aff_name):
                return True
    publisher_str = item.get("publisher", "")
    if institution_match(institution_name, publisher_str):
        return True
    return False

def get_created_year(item):
    """
    Returns the year from the item's 'created' field.
    For example, if item['created']['date-parts'] = [[2020, 7, 12]],
    returns 2020.
    """
    created_data = item.get("created", {})
    date_parts = created_data.get("date-parts", [])
    if date_parts and len(date_parts[0]) > 0:
        return date_parts[0][0]
    return None

def check_created_year_in_range(item, scholarship_year, delta=5):
    """
    Returns True if the item's created year is within ±delta of scholarship_year.
    """
    cyear = get_created_year(item)
    if cyear is None:
        return False
    return (scholarship_year - delta) <= cyear <= (scholarship_year + delta)

def compute_similarity_score(item, full_name, institution_name, scholarship_year, debug=False):
    """
    Computes a score as follows:
      +1 if a perfect name match is found—that is, if all tokens from full_name (the query)
         are present in the combined tokens from an author's 'given' and 'family' fields.
      +1 if the affiliation or publisher matches the institution.
      +1 if the created year is within ±5 years of scholarship_year.
    If no perfect name match is found for any author, returns -999.
    """
    query_tokens = set(tokenize_name_fields(full_name))
    perfect_match = False
    for au in item.get("author", []):
        author_tokens = set(tokenize_name_fields(au.get("given", ""), au.get("family", "")))
        if query_tokens.issubset(author_tokens):
            perfect_match = True
            break
    if not perfect_match:
        if debug:
            print("DEBUG: Perfect name match not found; score = -999")
        return -999

    score = 1  # perfect name match
    if check_affiliation_or_publisher(item, institution_name):
        score += 1
    if check_created_year_in_range(item, scholarship_year, delta=5):
        score += 1
    if debug:
        print(f"DEBUG: Computed similarity score = {score}")
    return score

def search_doi(name_query, given_name, scholarship_year, institution_name, debug=False):
    """
    Uses CrossRef to query for works by the author.
    We build the query using the combined last names only, then score each item
    (using the full name as 'given_name apellido1 apellido2').

    Returns ONLY ONE DOI (as a list with one dictionary):
      - If any item has a similarity score >= 2, returns the one with the highest score.
      - Otherwise, falls back to an exact token match and returns that DOI.
      - If none are found, returns None.
    """
    _, apellido1_parsed, apellido2_parsed = parse_spanish_name(name_query)
    last_name_query = apellido1_parsed if apellido1_parsed else ""
    if apellido2_parsed:
        last_name_query += " " + apellido2_parsed

    full_name = f"{given_name} {last_name_query}".strip()

    my_etiquette = Etiquette(
        'GoogleScholarWebScraper', '1.0',
        'https://github.com/juanceresa/GoogleScholarWebScraper',
    )
    works = Works(etiquette=my_etiquette)

    if debug:
        print(f"DEBUG: CROSSREF => searching for author last names='{last_name_query}' (limit 100 rows)...")

    results = works.query(author=last_name_query).sample(100)

    scored_items = []       # list of tuples: (score, item)
    exact_match_items = []  # items with an exact token match on the name

    for item in results:
        title = item.get("title")
        title_str = title[0] if title and len(title) > 0 else "N/A"
        if debug:
            print(f"DEBUG: Checking item => DOI='{item.get('DOI')}', TITLE='{title_str}'")

        sc = compute_similarity_score(item, full_name, institution_name, scholarship_year, debug=debug)
        if sc > 0:
            scored_items.append((sc, item))

        authors = item.get("author", [])
        for au in authors:
            if name_tokens_exact_match(au, full_name, debug=debug):
                exact_match_items.append(item)
                break

    scored_items.sort(key=lambda x: x[0], reverse=True)
    best_scored = [(sc, it) for (sc, it) in scored_items if sc >= 2]

    if best_scored:
        best_score, best_item = best_scored[0]
        doi_val = best_item.get("DOI")
        if doi_val:
            return [{"doi": f"https://doi.org/{doi_val}", "score": best_score}]
        else:
            return None

    if exact_match_items:
        fallback_item = exact_match_items[0]
        doi_val = fallback_item.get("DOI")
        if doi_val:
            return [{"doi": f"https://doi.org/{doi_val}", "score": 1}]
        else:
            return None

    return None


def search_google_scholar(name_query, scraper_user, scraper_pass):
    """
    Searches Google Scholar for an author profile using Oxylabs Web Unblocker.
    Returns the full profile URL if found (only if the link includes a unique user identifier).
    """
    # Construct the search URL.
    search_url = f"https://scholar.google.com/scholar?q={name_query.replace(' ', '+')}"

    # Define Web Unblocker proxy settings.
    proxies = {
        'http': f'http://{scraper_user}:{scraper_pass}@unblock.oxylabs.io:60000',
        'https': f'https://{scraper_user}:{scraper_pass}@unblock.oxylabs.io:60000',
    }

    try:
        # Send the request via the Web Unblocker.
        response = requests.get(search_url, proxies=proxies, verify=False, timeout=20)
        response.raise_for_status()

        # Parse the returned HTML.
        soup = BeautifulSoup(response.text, "html.parser")
        # Uncomment the next line to inspect the HTML structure for debugging:
        # print(soup.prettify())

        profile_link = None

        # Look for all anchor tags with an href attribute.
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # We want links that start with "/citations" AND contain a user parameter.
            if href.startswith("/citations") and "user=" in href:
                classes = a.get("class", [])
                # Check that the anchor has the expected profile button class.
                if "gs_btnPRO" in classes:
                    profile_link = href
                    break

        if profile_link:
            # If the URL is relative, prepend the base URL.
            if profile_link.startswith("/"):
                profile_link = "https://scholar.google.com" + profile_link
            return profile_link
        else:
            print(f"No valid profile link found for '{name_query}'")
            return None

    except requests.exceptions.HTTPError as err:
        print(f"HTTP Error: {err}")
        print(f"Response content: {response.content.decode()}")
        return None
    except Exception as e:
        print(f"Error fetching Google Scholar profile for '{name_query}': {e}")
        return None