"""
PreSales tools — everything needed to prep for a call or a demo: full
context on one deal, what's actually being proposed, and a single-call
360 view of an account across sales, support, and marketing history.
"""

import json

import sf_client
from mcp_instance import mcp


@mcp.tool()
def sf_get_opportunity_detail(opportunity_id: str) -> str:
    """
    Full detail on one Opportunity: stage, amount, close date, next
    steps, and description — plus its Contact roles, so you can see who
    on the customer side is involved. Use this to prep for a call.
    """
    opp_soql = (
        "SELECT Id, Name, Account.Name, Account.Industry, StageName, Amount, "
        "Probability, CloseDate, NextStep, Description, Owner.Name, Type, "
        "LeadSource FROM Opportunity WHERE Id = "
        f"'{opportunity_id}'"
    )
    opp = sf_client.query(opp_soql)
    if not opp:
        return json.dumps({"error": f"No Opportunity found with Id {opportunity_id}"})

    roles_soql = (
        "SELECT Contact.Name, Contact.Title, Contact.Email, Role, IsPrimary "
        f"FROM OpportunityContactRole WHERE OpportunityId = '{opportunity_id}'"
    )
    roles = sf_client.query(roles_soql)

    result = opp[0]
    result["contact_roles"] = roles
    return json.dumps(result, indent=2)


@mcp.tool()
def sf_get_opportunity_products(opportunity_id: str) -> str:
    """
    List the products/line items quoted on an Opportunity — product
    name, quantity, unit price, and total price. Use this to see
    exactly what's being proposed before a technical validation call or
    a demo, so the demo matches the deal.
    """
    soql = (
        "SELECT Product2.Name, Quantity, UnitPrice, TotalPrice, Description "
        f"FROM OpportunityLineItem WHERE OpportunityId = '{opportunity_id}'"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_account_360(account_id: str) -> str:
    """
    A single-call full picture of one Account: core details, open
    Opportunities, open Cases, Contacts, and the 10 most recent
    activities (Tasks/Events). Use this whenever you need "everything
    about this customer" in one shot — before a QBR, a renewal call, or
    a support escalation review.
    """
    account = sf_client.query(
        "SELECT Id, Name, Industry, Type, AnnualRevenue, NumberOfEmployees, "
        f"Description, Owner.Name FROM Account WHERE Id = '{account_id}'"
    )
    if not account:
        return json.dumps({"error": f"No Account found with Id {account_id}"})

    opportunities = sf_client.query(
        "SELECT Id, Name, StageName, Amount, CloseDate, IsClosed FROM Opportunity "
        f"WHERE AccountId = '{account_id}' ORDER BY CloseDate DESC LIMIT 20"
    )
    cases = sf_client.query(
        "SELECT Id, CaseNumber, Subject, Priority, Status, CreatedDate FROM Case "
        f"WHERE AccountId = '{account_id}' ORDER BY CreatedDate DESC LIMIT 20"
    )
    contacts = sf_client.query(
        "SELECT Id, Name, Title, Email, Phone FROM Contact "
        f"WHERE AccountId = '{account_id}' LIMIT 50"
    )
    recent_activity = sf_client.query(
        "SELECT Subject, ActivityDate, Status, Description FROM Task "
        f"WHERE AccountId = '{account_id}' ORDER BY ActivityDate DESC LIMIT 10"
    )

    result = account[0]
    result["opportunities"] = opportunities
    result["cases"] = cases
    result["contacts"] = contacts
    result["recent_activity"] = recent_activity
    return json.dumps(result, indent=2)


@mcp.tool()
def sf_create_contact(account_id: str, first_name: str, last_name: str, email: str = "", title: str = "") -> str:
    """
    MUTATING — creates a new Contact under an Account. Use this to add
    a stakeholder you've identified on a deal (e.g. a new champion or
    technical evaluator) without leaving the conversation. Returns the
    new Contact Id.
    """
    contact = {
        "AccountId": account_id,
        "FirstName": first_name,
        "LastName": last_name,
        "Email": email,
        "Title": title,
    }
    result = sf_client.create("Contact", contact)
    return json.dumps(result, indent=2)


@mcp.tool()
def sf_update_contact(contact_id: str, title: str = "", email: str = "", phone: str = "") -> str:
    """
    MUTATING — updates an existing Contact's title, email, or phone.
    Only pass the fields you actually want changed. Useful when a
    stakeholder changes roles, or you learn their correct contact
    details mid-conversation. To rename a contact or move them to a
    different Account, use sf_query to confirm the exact field names
    first — those are less common edits and this tool deliberately
    only covers the fields people actually update in practice.
    """
    fields = {}
    if title:
        fields["Title"] = title
    if email:
        fields["Email"] = email
    if phone:
        fields["Phone"] = phone
    if not fields:
        return json.dumps({"error": "No fields provided to update."})
    sf_client.update("Contact", contact_id, fields)
    return json.dumps({"updated": contact_id, "fields": fields}, indent=2)
