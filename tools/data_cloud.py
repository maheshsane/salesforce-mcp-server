"""
Data 360 (formerly Data Cloud) tools -- unified customer profile and
data lake queries. Uses the same Salesforce connection as sf_query
(see dc_client.py for why this doesn't need separate auth, unlike
Marketing Cloud) -- no additional setup required beyond what
setup_salesforce_auth.py already did.
"""

import json

import dc_client
from mcp_instance import mcp


@mcp.tool()
def dc_query(sql: str) -> str:
    """
    Run an ANSI SQL query (not SOQL) against Data 360 objects and
    return every matching row. Data 360 is where harmonized,
    identity-resolved customer data lives if your org has it set up --
    a single unified profile stitched together across web, email, and
    CRM activity, which core Salesforce's Account/Contact objects
    don't give you on their own.

    Object naming conventions to expect in queries:
      __dll   Data Lake Objects (raw ingested data, before harmonization)
      __dlm   Data Model Objects (harmonized schema), prefixed std__
              (current) or ssot__ (legacy)

    Example -- basic profile query:
      "SELECT FirstName__c, LastName__c, Email__c FROM Individual__dlm LIMIT 20"

    Segments are queryable here too, without any separate segment API --
    every time a segment is published, Data 360 auto-generates a regular
    DMO for it: {ObjectName}_SM__dlm (current members) and
    {ObjectName}_SMH__dlm (history, including who was added or dropped
    between publishes, via a Delta_Type__c field of New/Existing/Removed).

    Example -- who's currently in a published "Individual" segment:
      "SELECT Id__c, Segment_Id__c FROM Individual_SM__dlm"

    Example -- who dropped out of the segment since the last publish:
      "SELECT Id__c FROM Individual_SMH__dlm WHERE Delta_Type__c = 'Removed'"

    Finding the exact object and field API names for your org
    currently requires the Data 360 Setup UI's Data Model tab -- there
    is no describe-style tool for this yet (unlike sf_describe_object
    for core Salesforce). See CONTRIBUTING.md if you want to add one
    once you've confirmed the right metadata endpoint for your org's
    API version.
    """
    return json.dumps(dc_client.query(sql), indent=2)
