import csv
import io
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
import httpx
from firebase_admin import firestore
from app.core.config import settings


def clean_phone_number(raw_phone: str) -> str:
    """Normalize raw phone number to clean Pakistani +92 format or standard E.164."""
    if not raw_phone:
        return ""
    p = raw_phone.strip()
    if p.lower().startswith("p:"):
        p = p[2:].strip()
    # Strip any non-digit, non-plus characters
    cleaned = re.sub(r"[^\d+]", "", p)
    if cleaned.startswith("0092"):
        cleaned = "+92" + cleaned[4:]
    elif cleaned.startswith("92") and not cleaned.startswith("+"):
        cleaned = "+92" + cleaned[2:]
    elif cleaned.startswith("03") and len(cleaned) == 11:
        cleaned = "+92" + cleaned[1:]
    
    # Format +92 3XX XXXXXXX nicely if 13 chars
    if cleaned.startswith("+92") and len(cleaned) == 13:
        return f"{cleaned[:3]} {cleaned[3:6]} {cleaned[6:]}"
    return cleaned


def parse_location(raw_loc: str) -> Tuple[str, str]:
    """Extract City and specific Area from raw location string."""
    if not raw_loc or raw_loc.strip() == "":
        return ("Karachi", "Karachi Central")
    
    loc_clean = raw_loc.strip()
    loc_lower = loc_clean.lower()

    # Detect known cities
    cities = [
        ("hyderabad", "Hyderabad"),
        ("latifabad", "Hyderabad"),
        ("lahore", "Lahore"),
        ("islamabad", "Islamabad"),
        ("rawalpindi", "Rawalpindi"),
        ("faisalabad", "Faisalabad"),
        ("multan", "Multan"),
        ("peshawar", "Peshawar"),
        ("quetta", "Quetta"),
        ("sukkur", "Sukkur"),
        ("toba tek singh", "Toba Tek Singh"),
    ]

    for keyword, city_name in cities:
        if keyword in loc_lower:
            return (city_name, loc_clean)

    # Defaults to Karachi if it mentions Karachi or common Karachi neighborhoods
    karachi_keywords = [
        "karachi", "dha", "clifton", "gulshan", "korangi", "nazimabad", 
        "liaquatabad", "liaqutabad", "pechs", "p.e.c.h.s", "malir", "site", 
        "orangi", "baldia", "saddar", "scheme 33", "bahadurabad", "fb area", 
        "federal b", "jauhar", "johar", "defence", "north", "gulistan"
    ]
    for kw in karachi_keywords:
        if kw in loc_lower:
            return ("Karachi", loc_clean)

    # If no city matched, capitalize the first part or default to Karachi
    return ("Karachi", loc_clean)


def humanize_text(raw_text: str) -> str:
    """Turn snake_case / raw identifiers into clean, readable text."""
    if not raw_text:
        return ""
    text = raw_text.strip().replace("_", " ").replace("-", " ")
    # Special cases
    text = re.sub(r"\s+", " ", text)
    return text.title()


async def sync_meta_leads_from_sheets(db) -> Dict[str, Any]:
    """
    Asynchronously reads all Google Sheet CSV export URLs configured in settings,
    parses, cleans, deduplicates, and upserts them into the Firestore `facebook_leads` collection.
    Preserves existing Admin updates (status and custom notes).
    """
    sheet_urls = getattr(settings, "META_LEAD_SHEET_URLS", [])
    if not sheet_urls:
        return {"success": False, "message": "No sheet URLs configured", "synced": 0, "total": 0}

    all_parsed_leads: List[Dict[str, Any]] = []
    seen_lead_ids = set()
    seen_phones = set()

    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for url in sheet_urls:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    print(f"[Sheets Sync] HTTP status {resp.status_code} for {url[:50]}")
                    continue
                content = resp.text
                reader = list(csv.reader(io.StringIO(content)))
                if not reader or len(reader) < 2:
                    continue

                header = [c.strip().lower() for c in reader[0]]

                for row in reader[1:]:
                    if not row or not any(row):
                        continue

                    # Filter out Meta dummy test leads
                    row_str = " ".join(row).lower()
                    if "<test lead:" in row_str or "<dummy" in row_str:
                        continue

                    row_dict = {header[i]: row[i].strip() for i in range(min(len(header), len(row)))}

                    raw_id = row_dict.get("id", "").strip()
                    name = row_dict.get("full_name", "").strip()
                    phone = row_dict.get("phone", "").strip()
                    email = row_dict.get("email", "").strip()
                    created_time = row_dict.get("created_time", "").strip()
                    location_raw = (
                        row_dict.get("your_location_?")
                        or row_dict.get("your_location_")
                        or row_dict.get("location", "")
                    ).strip()
                    category_raw = (
                        row_dict.get("what_do_you_want_to_sell?")
                        or row_dict.get("what_do_you_want_to_sell")
                        or row_dict.get("category", "")
                    ).strip()
                    quantity_raw = (
                        row_dict.get("quantity_of_panels?")
                        or row_dict.get("quantity_of_panels")
                        or row_dict.get("quantity", "")
                    ).strip()
                    urgency_raw = (
                        row_dict.get("when_do_you_want_to_sell?")
                        or row_dict.get("when_do_you_want_to_sell")
                        or row_dict.get("urgency", "")
                    ).strip()
                    campaign_name = row_dict.get("campaign_name", "").strip()
                    form_name = row_dict.get("form_name", "").strip()
                    platform = row_dict.get("platform", "").strip().upper() or "FB"

                    # Handle Sheet 3 column shift (where name might be in phone col and phone in next col)
                    if not name and phone and not phone.startswith("+") and not phone.startswith("p:"):
                        name = phone
                        phone = ""
                        for val in row:
                            val_strip = val.strip()
                            if val_strip.startswith("p:+") or val_strip.startswith("+92") or (val_strip.isdigit() and len(val_strip) >= 10):
                                phone = val_strip
                                break

                    cleaned_phone = clean_phone_number(phone)

                    # Skip empty rows without name and phone
                    if not name and not cleaned_phone:
                        continue
                    if not name:
                        name = "Solar Scrap Lead"

                    # Deduplication check across sheets
                    norm_phone = re.sub(r"[^\d]", "", cleaned_phone)
                    if norm_phone and norm_phone in seen_phones:
                        continue
                    if raw_id and raw_id in seen_lead_ids:
                        continue

                    if norm_phone:
                        seen_phones.add(norm_phone)
                    if raw_id:
                        seen_lead_ids.add(raw_id)

                    city, area = parse_location(location_raw)
                    clean_category = humanize_text(category_raw) or "Solar Panels Scrap"
                    clean_quantity = humanize_text(quantity_raw)
                    clean_urgency = humanize_text(urgency_raw)

                    # Received Date parsing
                    received_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if created_time:
                        try:
                            # 2026-04-01T21:29:57-05:00
                            dt = datetime.fromisoformat(created_time)
                            received_date = dt.strftime("%Y-%m-%d")
                        except Exception:
                            if len(created_time) >= 10:
                                received_date = created_time[:10]

                    # Document ID determination
                    clean_id_suffix = raw_id.replace("l:", "").replace("-", "").strip()
                    if clean_id_suffix:
                        doc_id = f"meta_{clean_id_suffix}"
                        display_lead_id = f"FB{clean_id_suffix[-4:].upper()}"
                    elif norm_phone:
                        doc_id = f"meta_p_{norm_phone[-8:]}"
                        display_lead_id = f"FB{norm_phone[-4:]}"
                    else:
                        doc_id = f"meta_{len(all_parsed_leads)+1:04d}"
                        display_lead_id = f"FB{len(all_parsed_leads)+1:04d}"

                    note_lines = []
                    if clean_category:
                        note_lines.append(f"Item: {clean_category}")
                    if clean_quantity:
                        note_lines.append(f"Quantity: {clean_quantity}")
                    if clean_urgency:
                        note_lines.append(f"Timeline: {clean_urgency}")
                    if form_name:
                        note_lines.append(f"Form: {form_name}")
                    if campaign_name:
                        note_lines.append(f"Campaign: {campaign_name}")

                    lead_payload = {
                        "id": doc_id,
                        "lead_id": display_lead_id,
                        "meta_id": raw_id,
                        "name": name,
                        "phone": cleaned_phone,
                        "email": email,
                        "city": city,
                        "area": area,
                        "received_date": received_date,
                        "status": "New",
                        "source": f"Meta Ad ({platform or 'FB'}) - {form_name or 'Lead Ad'}",
                        "notes": [f"Meta Instant Form: {' | '.join(note_lines)}"] if note_lines else [],
                        "category": clean_category,
                        "quantity": clean_quantity,
                        "urgency": clean_urgency,
                        "platform": platform,
                        "campaign_name": campaign_name,
                        "form_name": form_name,
                    }
                    all_parsed_leads.append(lead_payload)

            except Exception as e:
                print(f"[Sheets Sync] Warning syncing URL {url}: {e}")

    # Upsert into Firestore in batches
    new_synced_count = 0
    updated_count = 0

    batch = db.batch()
    batch_count = 0

    # Fetch existing docs once for ultra-fast in-memory lookup
    existing_docs = {d.id: d.to_dict() for d in db.collection("facebook_leads").stream()}

    for lead in all_parsed_leads:
        doc_ref = db.collection("facebook_leads").document(lead["id"])
        
        if lead["id"] in existing_docs:
            existing_data = existing_docs[lead["id"]] or {}
            # Preserve existing Admin updates (e.g. status and notes)
            update_fields = {}
            if not existing_data.get("name") and lead["name"]:
                update_fields["name"] = lead["name"]
            if not existing_data.get("phone") and lead["phone"]:
                update_fields["phone"] = lead["phone"]
            if not existing_data.get("category") and lead["category"]:
                update_fields["category"] = lead["category"]
            if not existing_data.get("quantity") and lead["quantity"]:
                update_fields["quantity"] = lead["quantity"]

            if update_fields:
                batch.update(doc_ref, update_fields)
                batch_count += 1
                updated_count += 1
        else:
            # Create new lead document
            lead_data = {**lead}
            lead_data["created_at"] = firestore.SERVER_TIMESTAMP
            batch.set(doc_ref, lead_data)
            batch_count += 1
            new_synced_count += 1

        if batch_count >= 400:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    total_leads_in_db = len(list(db.collection("facebook_leads").stream()))

    return {
        "success": True,
        "synced": new_synced_count,
        "updated": updated_count,
        "total_scanned": len(all_parsed_leads),
        "total_leads": total_leads_in_db,
    }
