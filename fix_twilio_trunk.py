#!/usr/bin/env python3
"""
Fix Twilio Trunk Configuration for LiveKit Outbound Calls

This script configures Twilio to accept outbound calls from LiveKit.
The 403 Forbidden error occurs when Twilio blocks LiveKit's calls.

What this fixes:
1. Creates/updates a Twilio Elastic SIP Trunk
2. Assigns your phone number to the trunk
3. Configures the trunk to accept calls with proper authentication
"""

import os
from dotenv import load_dotenv
from twilio.rest import Client

# Load environment variables
load_dotenv('.env.local')

# Get credentials
account_sid = os.getenv('TWILIO_ACCOUNT_SID')
auth_token = os.getenv('TWILIO_AUTH_TOKEN')
phone_number = os.getenv('TWILIO_PHONE_NUMBER')

if not all([account_sid, auth_token, phone_number]):
    print("ERROR: Missing Twilio credentials in .env.local")
    exit(1)

# Initialize Twilio client
client = Client(account_sid, auth_token)

print("=" * 60)
print("Configuring Twilio for LiveKit Outbound Calls")
print("=" * 60)

# Step 1: Find or create an Elastic SIP Trunk
print("\n1. Checking for existing Elastic SIP Trunk...")
trunks = client.trunking.v1.trunks.list()

livekit_trunk = None
for trunk in trunks:
    if "LiveKit" in trunk.friendly_name or "livekit" in trunk.friendly_name.lower():
        livekit_trunk = trunk
        print(f"   ✓ Found existing trunk: {trunk.friendly_name}")
        break

if not livekit_trunk:
    print("   Creating new Elastic SIP Trunk...")
    livekit_trunk = client.trunking.v1.trunks.create(
        friendly_name="LiveKit Outbound Trunk",
        secure=False,  # Set to False for easier debugging
    )
    print(f"   ✓ Created new trunk: {livekit_trunk.sid}")

# Step 2: Assign phone number to trunk
print(f"\n2. Assigning phone number {phone_number} to trunk...")
try:
    # Check if already assigned
    current_numbers = livekit_trunk.phone_numbers.list()
    number_sids = [pn.sid for pn in current_numbers if pn.phone_number == phone_number]

    if number_sids:
        print(f"   ✓ Phone number already assigned")
    else:
        # Get the phone number resource
        phone = client.incoming_phone_numbers.list(phone_number=phone_number)[0]

        # Assign to trunk
        livekit_trunk.phone_numbers.create(phone_number_sid=phone.sid)
        print(f"   ✓ Phone number assigned successfully")
except Exception as e:
    print(f"   ⚠ Warning: {str(e)}")
    print("   You may need to assign the phone number manually in Twilio Console")

# Step 3: Configure credential authentication
print("\n3. Configuring authentication...")
print(f"   Trunk SID: {livekit_trunk.sid}")
print(f"   Trunk Domain: {livekit_trunk.domain_name}")
print(f"   Authentication: Username/Password")

# Step 4: Create credential list for LiveKit
print("\n4. Setting up credential list...")
try:
    # Check if credential list exists
    cred_lists = client.sip.credential_lists.list()
    livekit_cred_list = None

    for cl in cred_lists:
        if "LiveKit" in cl.friendly_name:
            livekit_cred_list = cl
            break

    if not livekit_cred_list:
        livekit_cred_list = client.sip.credential_lists.create(
            friendly_name="LiveKit Credentials"
        )
        print(f"   ✓ Created credential list: {livekit_cred_list.sid}")

    # Add/update credential
    credentials = livekit_cred_list.credentials.list()
    existing_cred = None
    for cred in credentials:
        if cred.username == account_sid:
            existing_cred = cred
            break

    if not existing_cred:
        livekit_cred_list.credentials.create(
            username=account_sid,
            password=auth_token
        )
        print(f"   ✓ Added credentials for LiveKit")
    else:
        print(f"   ✓ Credentials already configured")

    # Associate credential list with trunk
    try:
        livekit_trunk.credential_lists.create(
            credential_list_sid=livekit_cred_list.sid
        )
        print(f"   ✓ Associated credentials with trunk")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"   ✓ Credentials already associated with trunk")
        else:
            raise

except Exception as e:
    print(f"   ⚠ Warning: {str(e)}")

print("\n" + "=" * 60)
print("✅ Twilio Configuration Complete!")
print("=" * 60)
print(f"\nTrunk Domain: {livekit_trunk.domain_name}")
print(f"Use this address in LiveKit: {account_sid}.pstn.twilio.com")
print("\nYou can now test your outbound calls!")
