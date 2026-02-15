"""
Tournament Tracker - OP.GG MCP API
Fetches player data from OP.GG, calculates team scores, and writes to Google Sheets.
"""

import time
import re
import requests
from typing import Tuple, List, Dict
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ============================================================================
# CONFIGURATION
# ============================================================================

SERVICE_ACCOUNT_FILE = "gen-lang-client-0890515948-9d584c688878.json"
SOURCE_SHEET_ID = "1d5Fps2yHHfRwIy9KepptT3QsuG8m2c64bVCEeY-Jgds"
TARGET_SHEET_ID = "1_lLJHsz4tFLfDoeZw81U_-iA7U6VdkvCnVVwpg001jk"
TARGET_SHEET_NAME = "[#4.0] Teams"  # Production sheet

OPGG_MCP_URL = "https://mcp-api.op.gg/mcp"
REGIONS = ["eune", "euw", "tr", "ru", "na", "kr"]

LP_TABLE = {
    "IRON": {"IV": 0, "III": 100, "II": 200, "I": 300},
    "BRONZE": {"IV": 400, "III": 500, "II": 600, "I": 700},
    "SILVER": {"IV": 800, "III": 900, "II": 1000, "I": 1100},
    "GOLD": {"IV": 1200, "III": 1300, "II": 1400, "I": 1500},
    "PLATINUM": {"IV": 1600, "III": 1700, "II": 1800, "I": 1900},
    "EMERALD": {"IV": 2000, "III": 2100, "II": 2200, "I": 2300},
    "DIAMOND": {"IV": 2400, "III": 2500, "II": 2600, "I": 2700},
    "MASTER": 2800, "GRANDMASTER": 3000, "CHALLENGER": 3300,
}

TIER_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", 
              "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIV_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3, "4": 0, "3": 1, "2": 2, "1": 3}

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Player:
    discord: str
    tournament_account: str
    main_account: str
    role: str
    current_rank: str = "UNRANKED"
    current_lp: int = 0
    peak_rank: str = "UNRANKED"
    total_lp: int = 0
    opgg_tournament: str = ""
    opgg_main: str = ""
    region: str = ""

@dataclass  
class Team:
    name: str
    short_name: str
    logo_url: str
    description: str
    players: List[Player] = field(default_factory=list)
    regular_score: int = 0
    total_score: int = 0

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_credentials():
    return Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets", 
                "https://www.googleapis.com/auth/drive"]
    )

def parse_riot_id(riot_id: str) -> Tuple[str, str, str]:
    """Parse 'GameName#Tag' or 'GameName#Tag (region)' into (name, tag, region_hint)."""
    riot_id = riot_id.strip()
    
    # Extract parenthesized region hint, e.g. "Spoon#loh (euwest)" -> hint="euwest"
    region_hint = ""
    paren_match = re.search(r'\s*\(([^)]+)\)\s*$', riot_id)
    if paren_match:
        region_hint = paren_match.group(1).strip().lower()
        riot_id = riot_id[:paren_match.start()].strip()
    
    for sep in [" #", "#"]:
        if sep in riot_id:
            parts = riot_id.split(sep, 1)
            return parts[0].strip(), parts[1].strip() if len(parts) > 1 else "", region_hint
    return riot_id, "", region_hint

def parse_opgg_url(url: str) -> Tuple[str, str, str]:
    """Parse an OP.GG URL into (name, tag, region). Returns ('','','') on failure."""
    match = re.search(r'op\.gg/lol/summoners/([a-z]+)/([^/?#]+)-([^/?#]+)', url)
    if match:
        region = match.group(1)
        name = unquote(match.group(2).replace("+", " "))
        tag = unquote(match.group(3).replace("+", " "))
        return name, tag, region
    return "", "", ""

def get_opgg_url(name: str, tag: str, region: str) -> str:
    return f"https://op.gg/lol/summoners/{region}/{quote(name)}-{quote(tag)}"

def convert_drive_url(url: str) -> str:
    """Convert Google Drive URL to direct image URL for =IMAGE() formula."""
    if not url:
        return ""
    # Extract file ID from Drive URLs
    for pattern in [r'[?&]id=([a-zA-Z0-9_-]+)', r'/file/d/([a-zA-Z0-9_-]+)']:
        match = re.search(pattern, url)
        if match:
            return f"https://lh3.googleusercontent.com/d/{match.group(1)}"
    return url

def format_rank(tier: str, division: str) -> str:
    if not tier or tier.upper() in ["UNRANKED", "NULL"]:
        return "UNRANKED"
    tier = tier.upper()
    return tier if tier in ["MASTER", "GRANDMASTER", "CHALLENGER"] else f"{tier} {division}"

def calculate_lp(tier: str, division: str, lp: int) -> int:
    if not tier or tier == "UNRANKED":
        return 0
    tier = tier.upper()
    if tier in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        return LP_TABLE.get(tier, 0) + lp
    tier_data = LP_TABLE.get(tier)
    return tier_data.get(division, 0) + lp if isinstance(tier_data, dict) else 0

def compare_ranks(rank1: str, rank2: str) -> int:
    """Return 1 if rank1 > rank2, -1 if rank1 < rank2, 0 if equal."""
    if not rank1 or rank1 == "UNRANKED":
        return -1 if rank2 and rank2 != "UNRANKED" else 0
    if not rank2 or rank2 == "UNRANKED":
        return 1
    
    p1, p2 = rank1.upper().split(), rank2.upper().split()
    t1, t2 = p1[0], p2[0]
    d1 = p1[1] if len(p1) > 1 else "I"
    d2 = p2[1] if len(p2) > 1 else "I"
    
    ti1 = TIER_ORDER.index(t1) if t1 in TIER_ORDER else -1
    ti2 = TIER_ORDER.index(t2) if t2 in TIER_ORDER else -1
    
    if ti1 != ti2:
        return 1 if ti1 > ti2 else -1
    di1, di2 = DIV_ORDER.get(d1, 0), DIV_ORDER.get(d2, 0)
    return 1 if di1 > di2 else (-1 if di1 < di2 else 0)

# ============================================================================
# OP.GG API FUNCTIONS
# ============================================================================

def fetch_from_opgg(game_name: str, tag: str, region: str) -> Dict:
    """Fetch player profile from OP.GG MCP API."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "lol_get_summoner_profile",
            "arguments": {
                "game_name": game_name, "tag_line": tag, "region": region,
                "desired_output_fields": [
                    "data.summoner",
                ]
            }
        }
    }
    try:
        r = requests.post(OPGG_MCP_URL, json=payload, timeout=20)
        if r.status_code != 200:
            return None
        result = r.json()
        content = result.get("result", {}).get("content", [])
        return {"text": content[0].get("text", "")} if content else None
    except:
        return None

def parse_opgg_response(text: str) -> Dict:
    """Parse OP.GG response for rank data."""
    text = text.replace("\n", "").replace("\r", "")
    result = {"current_rank": "UNRANKED", "current_lp": 0, "peak_rank": "UNRANKED"}
    
    # Current solo rank (handles both formats)
    # Format 1: LeagueStat("SOLORANKED",TierInfo("GOLD",4,12))
    # Format 2: LeagueStat("SOLORANKED",TierInfo("GOLD",4,12,null,...))
    match = re.search(r'LeagueStat\("SOLORANKED",TierInfo\("([A-Z]+)",(\d+),(\d+)', text)
    if match:
        tier, div, lp = match.group(1), int(match.group(2)), int(match.group(3))
        div_roman = {1: "I", 2: "II", 3: "III", 4: "IV"}.get(div, "IV")
        result["current_rank"] = format_rank(tier, div_roman)
        result["current_lp"] = lp
        
    # Determine Peak Rank (Max of all sources)
    best = "UNRANKED"
    
    # Use current rank as starting point for peak
    if result["current_rank"] != "UNRANKED":
        best = result["current_rank"]
    
    # Check current split's "Top Tier" (RankEntrie1 format)
    # Format: RankEntrie1("SOLORANKED",RankInfo("SILVER",2,38,...))
    for tier, div in re.findall(r'RankEntrie1\("[A-Z]+",RankInfo\("([A-Z]+)",(\d+),', text):
        div_roman = {1: "I", 2: "II", 3: "III", 4: "IV"}.get(int(div), "IV")
        rank = format_rank(tier, div_roman)
        if compare_ranks(rank, best) > 0:
            best = rank
            
    # Check historical seasons (PreviousSeason format)
    # Format 1: PreviousSeason(31,TierInfo1("SILVER",3))  
    # Format 2: PreviousSeason(21,TierInfo("GRANDMASTER",1,640,null,...))
    for tier, div in re.findall(r'PreviousSeason\(\d+,TierInfo\d*\("([A-Z]+)",(\d+)', text):
        div_roman = {1: "I", 2: "II", 3: "III", 4: "IV"}.get(int(div), "IV")
        rank = format_rank(tier, div_roman)
        if compare_ranks(rank, best) > 0:
            best = rank
    
    result["peak_rank"] = best
    return result

def fetch_player(player: Player) -> Player:
    """Fetch and populate player rank data."""
    if not player.main_account.strip():
        print(f"  [Empty]: Field is empty")
        return player
    
    # Check if main_account is an OP.GG URL
    region_hint = ""
    if "op.gg/" in player.main_account:
        name, tag, region_hint = parse_opgg_url(player.main_account)
    else:
        name, tag, region_hint = parse_riot_id(player.main_account)
    
    if not name or not tag:
        print(f"  {player.main_account}: Invalid format")
        return player
    
    # Store cleaned name back on the player for sheet display
    player.main_account = f"{name}#{tag}"
    
    print(f"  {name}#{tag}:", end=" ")
    
    # Normalize common region aliases
    REGION_ALIASES = {"euwest": "euw", "eueast": "eune", "euwe": "euw", "west": "euw", "east": "eune"}
    if region_hint:
        region_hint = REGION_ALIASES.get(region_hint, region_hint)
    
    # Build region priority: hint first, then tag, then defaults
    regions = []
    if region_hint:
        regions.append(region_hint)
    if tag.lower() in REGIONS:
        regions.append(tag.lower())
    regions.extend(REGIONS)
    regions = list(dict.fromkeys(regions))  # Remove duplicates
    
    for region in regions:
        resp = fetch_from_opgg(name, tag, region)
        if not resp:
            continue
        
        data = parse_opgg_response(resp["text"])
        if data["current_rank"] == "UNRANKED" and data["peak_rank"] == "UNRANKED":
            if name.lower() not in resp["text"].lower():
                continue
        
        player.current_rank = data["current_rank"]
        player.current_lp = data["current_lp"]
        player.peak_rank = data["peak_rank"]
        player.region = region
        player.opgg_main = get_opgg_url(name, tag, region)
        
        if player.current_rank != "UNRANKED":
            parts = player.current_rank.split()
            player.total_lp = calculate_lp(parts[0], parts[1] if len(parts) > 1 else "I", player.current_lp)
        
        # Handle tournament account (may also be an OP.GG URL)
        if "op.gg/" in player.tournament_account:
            t_name, t_tag, t_region = parse_opgg_url(player.tournament_account)
            if t_name and t_tag:
                player.tournament_account = f"{t_name}#{t_tag}"
                player.opgg_tournament = get_opgg_url(t_name, t_tag, t_region or region)
        else:
            t_name, t_tag, _ = parse_riot_id(player.tournament_account)
            if t_name and t_tag:
                player.tournament_account = f"{t_name}#{t_tag}"
                player.opgg_tournament = get_opgg_url(t_name, t_tag, region)
        
        print(f"{player.current_rank} ({player.current_lp} LP) | Peak: {player.peak_rank}")
        return player
    
    print("Not found")
    return player

# ============================================================================
# GOOGLE SHEETS FUNCTIONS
# ============================================================================

def read_teams(creds) -> List[Team]:
    """Read team registrations from the source sheet."""
    gc = gspread.authorize(creds)
    records = gc.open_by_key(SOURCE_SHEET_ID).sheet1.get_all_values()
    if not records:
        return []
    
    headers = records[0]
    data_rows = records[1:]
    
    # Key for the logo correction column
    LOGO_2_KEY = "Komandas Logo (IZMANTO ŠO TIKAI TĀDOS GADĪJUMOS, JA EDITOJOT RESPONSE NEJAUŠI IELIKI NEPAREIZU BILDI PIRMAJĀ KOMANDAS LOGO JAUTĀJUMĀ!!!)"
    
    teams = []
    
    for row_data in data_rows:
        # Create a dict manually, handling potential length mismatch
        row = {h: (row_data[i] if i < len(row_data) else "") for i, h in enumerate(headers)}
        # Logo selection logic: prefer Logo 2 (correction), then Logo 1, else empty
        logo1 = row.get("Komandas Logo", "")
        logo2 = row.get(LOGO_2_KEY, "")
        final_logo = logo2 if logo2 else logo1

        team = Team(
            name=row.get("Komandas nosaukums", ""),
            short_name=row.get("Komandas saīsinātais nosaukums", ""),
            logo_url=final_logo,
            description=row.get("Komandas apraksts ", ""),
        )
        # 5 main players
        for i in range(1, 6):
            team.players.append(Player(
                discord=row.get(f"{i}. Discord profils", ""),
                tournament_account=row.get(f"{i}. Turnīra profils", ""),
                main_account=row.get(f"{i}. Main profils", ""),
                role=row.get(f"{i}. Role", ""),
            ))
        # 2 fill players
        for i in range(6, 8):
            team.players.append(Player(
                discord=row.get(f"{i}. Discord profils", ""),
                tournament_account=row.get(f"{i}. Turnīra profils", ""),
                main_account=row.get(f"{i}. Main profils", ""),
                role="FILL",
            ))
        teams.append(team)
    return teams

def write_teams(creds, teams: List[Team]):
    """Write team data to the target sheet."""
    gc = gspread.authorize(creds)
    worksheet = gc.open_by_key(TARGET_SHEET_ID).worksheet(TARGET_SHEET_NAME)
    sheets = build('sheets', 'v4', credentials=creds)
    sheet_id = worksheet.id
    
    TEAM_START, ROWS_PER_TEAM = 5, 7
    
    for idx, team in enumerate(teams, 1):
        print(f"  Writing team {idx}: {team.name}")
        row = TEAM_START + (idx - 1) * ROWS_PER_TEAM
        
        # Batch update text values
        updates = [
            {"range": f"C{row}", "values": [[team.name]]},
            {"range": f"C{row + 5}", "values": [[f"[{team.short_name}]"]]},
            {"range": f"S{row}", "values": [[team.regular_score]]},
            {"range": f"S{row + 5}", "values": [[f"[{team.total_score}]"]]}, # Total score in brackets
        ]
        
        for i, p in enumerate(team.players):
            r = row + i
            updates.extend([
                {"range": f"K{r}", "values": [[p.discord]]},
                {"range": f"L{r}", "values": [[p.tournament_account]]},
                {"range": f"M{r}", "values": [[p.main_account]]},
                {"range": f"O{r}", "values": [[p.peak_rank]]},
                {"range": f"Q{r}", "values": [[p.current_rank]]},
                {"range": f"R{r}", "values": [[p.total_lp]]},
            ])
        
        worksheet.batch_update(updates)
        
        # Formula updates (logo + hyperlinks)
        formula_reqs = []
        
        # Logo handling: Image formula OR text fallback
        logo_url = convert_drive_url(team.logo_url)
        if logo_url:
            # It's an image
            formula_reqs.append({
                "updateCells": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row-1, "endRowIndex": row,
                              "startColumnIndex": 6, "endColumnIndex": 7},
                    "rows": [{"values": [{"userEnteredValue": {"formulaValue": f'=IMAGE("{logo_url}")'}}]}],
                    "fields": "userEnteredValue",
                }
            })
        else:
            # Fallback: short name as text
            # We use updateCells here to overwrite any previous IMAGE formula with a string
            formula_reqs.append({
                "updateCells": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row-1, "endRowIndex": row,
                              "startColumnIndex": 6, "endColumnIndex": 7},
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": f"[{team.short_name}]"}}]}],
                    "fields": "userEnteredValue",
                }
            })
        
        for i, p in enumerate(team.players):
            r = row + i
            if p.opgg_tournament:
                formula_reqs.append({
                    "updateCells": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r-1, "endRowIndex": r,
                                  "startColumnIndex": 11, "endColumnIndex": 12},
                        "rows": [{"values": [{"userEnteredValue": 
                            {"formulaValue": f'=HYPERLINK("{p.opgg_tournament}","{p.tournament_account.replace(chr(34), chr(39))}")'}}]}],
                        "fields": "userEnteredValue",
                    }
                })
            if p.opgg_main:
                formula_reqs.append({
                    "updateCells": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r-1, "endRowIndex": r,
                                  "startColumnIndex": 12, "endColumnIndex": 13},
                        "rows": [{"values": [{"userEnteredValue": 
                            {"formulaValue": f'=HYPERLINK("{p.opgg_main}","{p.main_account.replace(chr(34), chr(39))}")'}}]}],
                        "fields": "userEnteredValue",
                    }
                })
        
        if formula_reqs:
            try:
                sheets.spreadsheets().batchUpdate(
                    spreadsheetId=TARGET_SHEET_ID, body={"requests": formula_reqs}
                ).execute()
            except Exception as e:
                print(f"    Warning: {e}")
        
        time.sleep(0.3)
    
    print(f"\nData written to: {TARGET_SHEET_NAME}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 60)
    print("Tournament Tracker")
    print("=" * 60)
    
    print("\n[1/4] Authenticating...")
    creds = get_credentials()
    
    print("\n[2/4] Reading form responses...")
    teams = read_teams(creds)
    print(f"  Found {len(teams)} teams")
    
    print("\n[3/4] Fetching player data...")
    for team in teams:
        print(f"\n{team.name}:")
        for player in team.players:
            fetch_player(player)
            time.sleep(0.2)
        team.regular_score = sum(p.total_lp for p in team.players[:5])
        team.total_score = sum(p.total_lp for p in team.players)
        print(f"  Scores: Regular={team.regular_score}, Total={team.total_score}")
    
    teams.sort(key=lambda t: t.regular_score, reverse=True)
    
    print("\n[4/4] Writing to Google Sheets...")
    write_teams(creds, teams)
    
    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)

if __name__ == "__main__":
    main()
