import json
import os

import boto3
import requests
from bs4 import BeautifulSoup

# Config
CITIES = {
    'Garden City': 1119,
    'Boise': 1020,
    'Caldwell': 1021,
    'Nampa': 1022,
}
VEHICLES = [
    {'make': 'HONDA', 'model': 'ELEMENT'},
    {'make': 'ACURA', 'model': 'RSX'},
]
TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'jalopper-inventory')
EMAIL_TO = os.environ.get('EMAIL_TO')      # required; set as a Lambda environment variable
EMAIL_FROM = os.environ.get('EMAIL_FROM')  # required; must be verified in SES
AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')

# Seconds to wait on each inventory request. The handler makes one request per
# (vehicle, city) pair, so keep (pairs x timeout) comfortably under the Lambda timeout.
REQUEST_TIMEOUT = 5

dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
ses = boto3.client('ses', region_name=AWS_REGION)


def get_current_inventory(yard_id, make, model):
    """Scrape current inventory for a given yard and vehicle.

    Returns a list of rows (an empty list means the yard has none of this vehicle),
    or None if the scrape failed (network error, timeout, or non-2xx response).
    """
    url = f"https://inventory.pickapartjalopyjungle.com/?YardId={yard_id}&VehicleMake={make}&VehicleModel={model}"
    try:
        response = requests.post(url, json={}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Scrape failed for yard {yard_id} {make} {model}: {e}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')

    table = soup.find('table')
    if not table:
        return []

    data = []
    for row in table.find_all('tr'):
        cols = [td.text.strip() for td in row.find_all('td')]
        cols = [c for c in cols if c]
        if cols:
            data.append(cols)
    return data


def get_stored_inventory(city):
    """Get previously stored inventory from DynamoDB.

    Returns a list (empty if nothing has been stored yet), or None if the read failed.
    """
    table = dynamodb.Table(TABLE_NAME)
    try:
        response = table.get_item(Key={'city': city})
        if 'Item' in response:
            return [list(item) for item in json.loads(response['Item']['inventory'])]
        return []
    except Exception as e:
        print(f"Error reading DynamoDB for {city}: {e}")
        return None


def store_inventory(city, inventory):
    """Store current inventory to DynamoDB."""
    table = dynamodb.Table(TABLE_NAME)
    try:
        table.put_item(Item={
            'city': city,
            'inventory': json.dumps(inventory)
        })
    except Exception as e:
        print(f"Error writing DynamoDB for {city}: {e}")


def send_email(subject, body):
    """Send notification email via SES."""
    ses.send_email(
        Source=EMAIL_FROM,
        Destination={'ToAddresses': [EMAIL_TO]},
        Message={
            'Subject': {'Data': subject},
            'Body': {'Text': {'Data': body}}
        }
    )


def lambda_handler(event, context):
    if not EMAIL_TO or not EMAIL_FROM:
        raise RuntimeError("EMAIL_TO and EMAIL_FROM environment variables must be set")

    all_new = {}
    to_store = {}
    failed = []

    for vehicle in VEHICLES:
        make = vehicle['make']
        model = vehicle['model']
        vehicle_label = f"{make} {model}"

        for city, yard_id in CITIES.items():
            key = f"{city} - {vehicle_label}"
            print(f"Checking {key} (yard {yard_id})...")

            current = get_current_inventory(yard_id, make, model)
            if current is None:
                # Scrape failed: leave stored data alone so a hiccup can't wipe it.
                failed.append(key)
                continue

            stored = get_stored_inventory(key)
            if stored is None:
                # Couldn't read the baseline: skip rather than treat everything as new.
                failed.append(key)
                continue

            # Find new entries not in stored inventory
            new_items = [item for item in current if item not in stored]

            if new_items:
                print(f"  NEW at {key}: {new_items}")
                all_new[key] = new_items
            else:
                print(f"  No new cars at {key}")

            if not current and stored:
                # An empty result after a non-empty one is ambiguous (cars sold, or a
                # bad page). Keep the old baseline; it is replaced as soon as the yard
                # has cars again.
                print(f"  Empty result for {key}; keeping previous stored inventory")
            elif current != stored:
                to_store[key] = current

    # Email first. If SES fails this raises, nothing is stored, and the next run
    # will find and report the same new vehicles again.
    if all_new:
        subject = "🚗 New vehicle(s) at Pick-a-Part!"
        body = "New vehicles found:\n\n"
        for location, items in all_new.items():
            body += f"📍 {location}:\n"
            for item in items:
                body += f"  • {' '.join(item)}\n"
            body += "\n"
        body += "Go get 'em!"

        send_email(subject, body)
        print(f"Email sent to {EMAIL_TO}")

    for key, inventory in to_store.items():
        store_inventory(key, inventory)

    if failed:
        print(f"Skipped {len(failed)} check(s) due to errors: {failed}")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'checked': [f"{v['make']} {v['model']}" for v in VEHICLES],
            'cities': list(CITIES.keys()),
            'new_finds': {loc: items for loc, items in all_new.items()},
            'failed': failed
        })
    }
