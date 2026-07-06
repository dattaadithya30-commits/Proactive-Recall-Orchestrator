import json
import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("MockEmailServer")

DRAFTS_FILE = "drafted_emails.json"

@mcp.tool()
def draft_email(to_email: str, subject: str, body: str, facility_name: str, contact_name: str = "Administrator") -> str:
    """
    Draft an email and queue it for sending.
    
    Args:
        to_email: Recipient's email address.
        subject: Email subject.
        body: Email body content.
        facility_name: The name of the facility being notified.
        contact_name: The name of the contact person.
    """
    draft = {
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "facility_name": facility_name,
        "contact_name": contact_name
    }
    
    # Read existing drafts
    drafts = []
    if os.path.exists(DRAFTS_FILE):
        try:
            with open(DRAFTS_FILE, "r") as f:
                drafts = json.load(f)
        except Exception:
            pass
            
    drafts.append(draft)
    
    with open(DRAFTS_FILE, "w") as f:
        json.dump(drafts, f, indent=4)
        
    return f"Email draft successfully created for {to_email} ({facility_name})."

@mcp.tool()
def clear_drafts() -> str:
    """
    Clear all previously saved drafts.
    """
    if os.path.exists(DRAFTS_FILE):
        try:
            os.remove(DRAFTS_FILE)
        except Exception:
            pass
    return "All drafts cleared."

if __name__ == "__main__":
    # Start the FastMCP stdio server
    mcp.run()
