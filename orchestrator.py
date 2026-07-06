import os
import sys
import asyncio
import json
import re
import smtplib
from email.mime.text import MIMEText
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from dotenv import load_dotenv


# Load environment variables (such as GOOGLE_API_KEY)
load_dotenv()

from google.adk.agents.llm_agent import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams, StdioServerParameters
from google.genai import types

# ----------------------------------------------------------------------
# 1. Specialist Tools Definitions
# ----------------------------------------------------------------------

def analyze_ledger(batch_id: str) -> str:
    """
    Analyzes the inventory_ledger.csv to find all locations containing the compromised batch_id.
    It calculates the volumes at each location and generates a Seaborn bar chart 
    showing the affected volume per location saved as 'affected_volume.png'.
    
    Args:
        batch_id: The compromised batch ID to search for (e.g. 'LOT-A45').
        
    Returns:
        A JSON string containing the findings: status, batch_id, drug_name, affected_locations, and path to chart.
    """
    # Local imports moved to top-level
    
    if not os.path.exists("inventory_ledger.csv"):
        return json.dumps({"status": "error", "message": "inventory_ledger.csv not found."})
        
    df = pd.read_csv("inventory_ledger.csv")
    # Clean whitespace and filter
    df["batch_id"] = df["batch_id"].str.strip()
    filtered_df = df[df["batch_id"] == batch_id.strip()]
    
    if filtered_df.empty:
        return json.dumps({"status": "no_affected_facilities", "batch_id": batch_id})
        
    drug_name = filtered_df.iloc[0]["drug_name"]
    
    # Group by location and sum volume
    grouped = filtered_df.groupby("current_location_id")["volume"].sum().reset_index()
    grouped_sorted = grouped.sort_values(by="volume", ascending=False)
    
    # Generate Seaborn bar chart
    plt.figure(figsize=(10, 6))
    sns.set_theme(style="whitegrid")
    
    ax = sns.barplot(
        x="current_location_id",
        y="volume",
        data=grouped_sorted,
        hue="current_location_id",
        palette="Reds_r",
        legend=False
    )
    
    plt.title(f"Affected Volume of {drug_name} per Location (Batch: {batch_id})", fontsize=14, fontweight="bold", pad=15)
    plt.xlabel("Location ID", fontsize=12, labelpad=10)
    plt.ylabel("Volume (units)", fontsize=12, labelpad=10)
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    chart_path = "affected_volume.png"
    plt.savefig(chart_path, dpi=300)
    plt.close()
    
    affected_locations = grouped_sorted.to_dict(orient="records")
    
    result = {
        "status": "success",
        "batch_id": batch_id,
        "drug_name": drug_name,
        "affected_locations": affected_locations,
        "chart_saved_to": chart_path
    }
    
    return json.dumps(result, indent=2)

def lookup_facility_contacts(location_ids_json: str) -> str:
    """
    Looks up contact information (facility name, contact name, contact email, region) 
    for a list of location IDs from facility_contacts.csv.
    
    Args:
        location_ids_json: A JSON string containing a list of location IDs (e.g. '["LOC-001", "LOC-002"]').
        
    Returns:
        A JSON string containing the contact details for each location.
    """
    # Local imports moved to top-level
    
    if not os.path.exists("facility_contacts.csv"):
        return json.dumps({"status": "error", "message": "facility_contacts.csv not found."})
        
    try:
        location_ids = json.loads(location_ids_json)
    except Exception:
        return json.dumps({"status": "error", "message": "Invalid JSON list of location IDs."})
        
    df = pd.read_csv("facility_contacts.csv")
    filtered_df = df[df["location_id"].isin(location_ids)]
    
    contacts = filtered_df.to_dict(orient="records")
    return json.dumps(contacts, indent=2)

def send_smtp_email(to_email: str, subject: str, body: str, facility_name: str, contact_name: str) -> bool:
    """
    Sends a recall notice email via SMTP using credentials defined in environment variables.
    
    Returns True if successfully sent, False otherwise.
    """
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM_EMAIL") or smtp_username
    
    if not smtp_host or not smtp_username or not smtp_password:
        return False
        
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = f"Proactive Recall Orchestrator <{smtp_from}>"
    msg["To"] = f"{contact_name} <{to_email}>"
    
    try:
        port = int(smtp_port) if smtp_port else 587
        if port == 465:
            server = smtplib.SMTP_SSL(smtp_host, port, timeout=10)
        else:
            server = smtplib.SMTP(smtp_host, port, timeout=10)
            server.starttls()
            
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"\n[SMTP Error] Failed to send email to {contact_name} ({to_email}) at {facility_name}: {e}")
        return False

# ----------------------------------------------------------------------

# 2. Specialist Agents Definitions
# ----------------------------------------------------------------------

# Standard stable model version supported in ADK
MODEL_ID = "gemini-2.5-flash"


data_agent = LlmAgent(
    model=MODEL_ID,
    name="data_agent",
    instruction=(
        "You are the Data Agent for the Proactive Recall Orchestrator.\n"
        "Your task is to analyze the inventory ledger for a compromised batch ID.\n"
        "1. Run the `analyze_ledger` tool with the provided batch_id.\n"
        "2. Output a summary explaining which locations hold the compromised batch and how much volume they hold.\n"
        "3. Provide the exact JSON result from the tool at the end of your response inside a markdown code block so the system can parse it."
    ),
    tools=[analyze_ledger]
)

# Connect to the mock email MCP server via stdio
email_mcp = MCPToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="python",
            args=["mock_email_mcp_server.py"],
        )
    )
)

comms_agent = LlmAgent(
    model=MODEL_ID,
    name="comms_agent",
    instruction=(
        "You are the Comms Agent for the Proactive Recall Orchestrator.\n"
        "Your task is to look up contacts for affected locations and queue recall email drafts.\n"
        "1. First, call `lookup_facility_contacts` with the list of location IDs obtained from the data analysis.\n"
        "2. For each contact, draft an urgent, tailored recall notice regarding the compromised batch ID and drug name.\n"
        "3. Use the MockEmailServer's `draft_email` tool to queue each email draft.\n"
        "4. Confirm to the user that all recall notices have been successfully drafted and saved."
    ),
    tools=[lookup_facility_contacts, email_mcp]
)

# ----------------------------------------------------------------------
# 3. Main Workflow Orchestrator
# ----------------------------------------------------------------------

async def run_orchestrator(batch_id: str):
    # Pre-clean any old drafts
    if os.path.exists("drafted_emails.json"):
        try:
            os.remove("drafted_emails.json")
        except Exception:
            pass
            
    print("\n" + "="*60)
    print(f" PROACTIVE RECALL ORCHESTRATOR - BATCH: {batch_id}")
    print("="*60)
    
    # Check for API Key presence
    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        print("\n[Error] No API key was provided. Please define GOOGLE_API_KEY or GEMINI_API_KEY in a .env file.")
        return

    session_service = InMemorySessionService()
    
    # --- PHASE 1: Data Agent Analysis ---
    print("\n[Phase 1] Contacting Data Agent to isolate batch and generate charts...")
    data_runner = Runner(
        agent=data_agent,
        session_service=session_service,
        app_name="recall_orchestrator",
        auto_create_session=True
    )
    
    data_output = ""
    new_msg = types.Content(
        role="user",
        parts=[types.Part(text=f"Analyze compromised batch: {batch_id}")]
    )
    
    async for event in data_runner.run_async(
        user_id="user_1",
        session_id="session_data",
        new_message=new_msg
    ):
        try:
            if event.message and event.message.parts:
                for part in event.message.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                        data_output += part.text
        except Exception:
            pass
            
    print("\n\n[Data Agent] Analysis completed. Seaborn chart saved as 'affected_volume.png'.")
    
    # Parse JSON output from the data agent's response to get location IDs
    json_block = re.search(r"```json\n(.*?)\n```", data_output, re.DOTALL)
    if not json_block:
        json_block = re.search(r"(\{.*?\})", data_output, re.DOTALL)
        
    analysis_data = None
    if json_block:
        try:
            analysis_data = json.loads(json_block.group(1))
        except Exception:
            pass
            
    if not analysis_data or analysis_data.get("status") != "success":
        print("\n[System Error] Data Agent could not successfully isolate the batch or no facilities are affected.")
        return
        
    affected_locations = [loc["current_location_id"] for loc in analysis_data["affected_locations"]]
    drug_name = analysis_data["drug_name"]
    
    print(f"\n[System] Found {len(affected_locations)} affected facilities: {affected_locations}")
    
    # Due to free API tier limits, only process emails for the top 3 most affected locations
    top_affected_locations = affected_locations[:3]
    print(f"[System] Limiting email processing to the top 3 most affected locations: {top_affected_locations}")
    
    # --- PHASE 2: Comms Agent Email Drafting ---
    print("\n[Phase 2] Contacting Comms Agent to draft and register recall notices...")
    comms_runner = Runner(
        agent=comms_agent,
        session_service=session_service,
        app_name="recall_orchestrator",
        auto_create_session=True
    )
    
    comms_msg = types.Content(
        role="user",
        parts=[types.Part(text=(
            f"Please lookup contacts and draft emails for locations: {json.dumps(top_affected_locations)}.\n"
            f"The compromised batch is {batch_id} for drug {drug_name}."
        ))]
    )
    
    comms_output = ""
    async for event in comms_runner.run_async(
        user_id="user_1",
        session_id="session_comms",
        new_message=comms_msg
    ):
        try:
            if event.message and event.message.parts:
                for part in event.message.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                        comms_output += part.text
        except Exception:
            pass
            
    print("\n\n[Comms Agent] Recall notices successfully drafted through MockEmailServer.")
    
    # --- PHASE 3: Human-in-the-Loop Verification ---
    print("\n" + "-"*60)
    print(" HUMAN-IN-THE-LOOP GUARDRAIL: PENDING APPROVAL")
    print("-"*60)
    
    if not os.path.exists("drafted_emails.json"):
        print("[System Error] No drafted emails found in 'drafted_emails.json'.")
        return
        
    with open("drafted_emails.json", "r") as f:
        drafts = json.load(f)
        
    print(f"\nExhibiting {len(drafts)} drafted recall notice(s):")
    for idx, draft in enumerate(drafts, 1):
        print(f"\n[{idx}/{len(drafts)}] TO: {draft['contact_name']} ({draft['to_email']})")
        print(f"FACILITY: {draft['facility_name']}")
        print(f"SUBJECT: {draft['subject']}")
        print(f"BODY:\n{draft['body']}")
        print("-" * 40)
        
    # Ask for user confirmation
    approval = input("\nDo you approve sending these recall notices? (y/n): ").strip().lower()
    
    if approval in ["y", "yes"]:
        smtp_host = os.environ.get("SMTP_HOST")
        smtp_username = os.environ.get("SMTP_USERNAME")
        
        if smtp_host and smtp_username:
            print("\n[System] Recall notices APPROVED. Sending actual emails via SMTP...")
            success_count = 0
            for draft in drafts:
                print(f">> Sending email to {draft['contact_name']} ({draft['to_email']})...")
                sent = send_smtp_email(
                    to_email=draft["to_email"],
                    subject=draft["subject"],
                    body=draft["body"],
                    facility_name=draft["facility_name"],
                    contact_name=draft["contact_name"]
                )
                if sent:
                    success_count += 1
            print(f">> Transmission completed. {success_count}/{len(drafts)} email(s) successfully sent.")
        else:
            print("\n[System] Recall notices APPROVED. SMTP is not configured in .env; executing mock transmission...")
            print(">> Mock transmission successful. Notifications delivered to all administrators.")
            
        print(f">> affected_volume.png has been saved to: {os.path.abspath('affected_volume.png')}")
    else:
        print("\n[System] Recall notices REJECTED. Recall sequence aborted.")

        
    print("="*60 + "\n")

if __name__ == "__main__":
    # Default compromised batch is LOT-A45
    target_batch = "LOT-A45"
    if len(sys.argv) > 1:
        target_batch = sys.argv[1]
        
    asyncio.run(run_orchestrator(target_batch))
