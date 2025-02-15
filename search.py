import re
import unidecode
from crossref.restful import Works, Etiquette
import requests

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
    """
    # Parse the user's name
    user_first, paternal, maternal = parse_spanish_name(user_full_name)
    if debug:
        print(f"DEBUG: Parsed user name: first={user_first}, paternal={paternal}, maternal={maternal}")
    if not paternal:
        return False

    # Build the combined last name string.
    combined_last = paternal
    if maternal:
        combined_last += " " + maternal
    # Normalize: remove accents, lowercase, replace hyphens with spaces.
    combined_last_norm = unidecode.unidecode(combined_last.lower()).replace('-', ' ')
    combined_tokens = combined_last_norm.split()

    # Get author's name fields.
    given  = unidecode.unidecode((author.get('given') or '').lower())
    family = unidecode.unidecode((author.get('family') or '').lower()).replace('-', ' ')

    if debug:
        print(f"DEBUG: Checking author => given='{given}', family='{family}'")
        if user_first:
            print(f"DEBUG: User primary first: {user_first[0]}")
        print(f"DEBUG: Combined last tokens: {combined_tokens}")

    # Check that the user's primary first name is present in the author's given field.
    if user_first:
        if user_first[0] not in given:
            if debug:
                print(f"DEBUG: FAIL => first token '{user_first[0]}' not in '{given}'")
            return False

    # Require that at least one token from the combined last name is found in family.
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
    combined = unidecode.unidecode(combined)  # Remove accents
    combined = combined.replace("-", " ")     # Replace hyphens with space
    combined = re.sub(r"[^\w\s]", "", combined) # Remove punctuation
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
    Computes a score:
      +1 for a name match (using our Spanish combined-name match),
      +1 if the affiliation or publisher matches the institution,
      +1 if the created year is within ±5 years of scholarship_year.
    If no name match is found, returns -999.
    """
    if not any_author_matches_name(item, full_name, debug=debug):
        if debug:
            print("DEBUG: Name did not match; score = -999")
        return -999
    score = 1  # name match
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

    Returns ONLY ONE DOI:
      - If any item has a similarity score >= 2, return the one with the highest score.
      - Otherwise, fallback to an exact token match (combining the author's 'given'
        and 'family' fields with hyphens removed) and return that DOI.
      - If none are found, returns None.
    """
    # Build the combined last name query from 'name_query'
    _, apellido1_parsed, apellido2_parsed = parse_spanish_name(name_query)
    last_name_query = apellido1_parsed if apellido1_parsed else ""
    if apellido2_parsed:
        last_name_query += " " + apellido2_parsed

    # Build full name for matching (e.g., "Rebeca Acin Perez")
    full_name = f"{given_name} {last_name_query}".strip()

    my_etiquette = Etiquette(
        'GoogleScholarWebScraper', '1.0',
        'https://github.com/juanceresa/GoogleScholarWebScraper',
        'jcere@umich.edu'
    )
    works = Works(etiquette=my_etiquette)

    if debug:
        print(f"DEBUG: CROSSREF => searching for author last names='{last_name_query}' (limit 100 rows)...")

    # Query CrossRef (sample up to 100 results)
    results = works.query(author=last_name_query).sample(100)

    scored_items = []       # list of tuples: (score, item)
    exact_match_items = []  # items with an exact token match on the name

    for item in results:
        title = item.get("title")
        title_str = title[0] if title and len(title) > 0 else "N/A"
        if debug:
            print(f"DEBUG: Checking item => DOI='{item.get('DOI')}', TITLE='{title_str}'")

        # Compute similarity score
        sc = compute_similarity_score(item, full_name, institution_name, scholarship_year, debug=debug)
        if sc > 0:
            scored_items.append((sc, item))

        # Check for exact token match based on combined given+family fields
        authors = item.get("author", [])
        for au in authors:
            if name_tokens_exact_match(au, full_name, debug=debug):
                exact_match_items.append(item)
                break

    # Sort scored items by descending score
    scored_items.sort(key=lambda x: x[0], reverse=True)
    # Filter for items with score >= 2
    best_scored = [(sc, it) for (sc, it) in scored_items if sc >= 2]

    if best_scored:
        best_score, best_item = best_scored[0]
        doi_val = best_item.get("DOI")
        if doi_val:
            return [{"doi": f"https://doi.org/{doi_val}", "score": best_score}]
        else:
            return None

    # Fallback: if no item with score >= 2, use the exact token match approach.
    if exact_match_items:
        fallback_item = exact_match_items[0]
        doi_val = fallback_item.get("DOI")
        if doi_val:
            return [{"doi": f"https://doi.org/{doi_val}", "score": 1}]
        else:
            return None

    return None

def search_google_scholar(name_query, scraper_api_url, scraper_user, scraper_pass, unblock_proxy):
    """
    Searches Google Scholar for a user profile using Oxylabs Real-Time Crawler.
    Returns a profile URL if found.
    """
    # We do a simple check for the first word of the name or the entire name.
    name_lower = name_query.lower()
    first_word = name_lower.split()[0]

    search_url = f"https://scholar.google.com/scholar?q={name_query.replace(' ', '+')}"
    payload = {"source": "google", "url": search_url, "parse": True}

    try:
        response = requests.post(
            scraper_api_url,
            auth=(scraper_user, scraper_pass),
            json=payload,
            proxies={"http": unblock_proxy, "https": unblock_proxy},
            verify=False,
            timeout=20
        )
        if response.status_code == 200:
            data = response.json()
            results = data.get("data", {}).get("results", [])
            for result in results:
                title = result.get("title", "")
                snippet = result.get("snippet", "")
                link = result.get("link", "")

                # Convert to lowercase for easier matching
                title_lower = title.lower()
                snippet_lower = snippet.lower()

                # Check if "user profiles for" appears in the title or snippet
                if "user profiles for" in title_lower or "user profiles for" in snippet_lower:
                    if name_lower in title_lower or name_lower in snippet_lower or first_word in snippet_lower:
                        possible_links = set()
                        if link:
                            possible_links.add(link)
                        for sub in result.get("inlineLinks", []):
                            if sub.get("link"):
                                possible_links.add(sub["link"])
                        for sub in result.get("relatedUrls", []):
                            if sub.get("link"):
                                possible_links.add(sub["link"])
                        for plink in possible_links:
                            if "scholar.google.com/citations?" in plink:
                                return plink
                if "scholar.google.com/citations?" in link:
                    return link
    except Exception as e:
        print(f"Error fetching Google Scholar profile for {name_query}: {e}")
    print(f"No profile found for {name_query}")
    return None