# CIDMAgent - Natural Language Prompts for Demo
## Windsurf/AI Office Hours with Zac Sync Call | 2026-04-10

> These are plain English prompts you can type/speak during the meeting.
> The AI assistant understands these and runs the right commands behind the scenes.

---

## PART 1: CREATING CUSTOMERS

### Basic Customer Creation (14 Countries)
- "Create a US customer"
- "Create a China customer"
- "Create an India customer"
- "Create a Germany customer"
- "Create a Brazil customer"
- "Create a Japan customer"
- "Create a Great Britain customer"
- "Create a customer for Italy"
- "Create a customer for Mexico"
- "Create a customer for Saudi Arabia"
- "Create a customer for Malaysia"
- "Create a customer for Belgium"
- "Create a customer for New Zealand"
- "Create a customer for Spain"

### Consumer / Individual Customers
- "Create a US consumer customer"
- "Create a US individual customer"
- "Create a China consumer customer"

### Specific Site Types
- "Create a billing only customer for US"
- "Create a shipping only customer for US"
- "Create a billing only customer for Germany"

### Bulk Creation
- "Create customers for all countries"
- "Create consumer customers for all countries"

---

## PART 2: VIEWING CUSTOMER DETAILS

### Party Level
- "Get the customer details for P15795588623"
- "Show me the party hierarchy for P15795588623"
- "What are the sites for party P15795588623"
- "Show me the contacts for P15795588623"
- "Get the customer notes for P15795588623"
- "Show me the relationships for P15795588623"
- "Get customer accounts for P15795588623"

### Specific Entity
- "Get site details for P15795588623 site S16004244180"
- "Show me contact R13749148428 for party P15795588623"
- "Get invoice profile for D11320815907 contact R13749148428"

---

## PART 3: ADDING ENTITIES TO EXISTING CUSTOMER

### Sites
- "Add a billing site to party P15795588623 for US"
- "Add a shipping site to party P15795588623 for US"
- "Add a billing site to P15795588623 for China"

### Contacts
- "Add a contact to party P15795588623 site S16004244180 for US"
- "Extend the billing contact R13749148428 to shipping for party P15795588623 site S16004244180"

### Notes
- "Add a note to party P15795588623 for US"
- "Add a note to party P15795588623 saying QA validation complete"

### Relationships
- "Create a partner to sales relationship between P15795588623 and P15795467890"
- "Create a bill-to relationship between P15795588623 and P15795467890"
- "Create a funder relationship between P15795588623 and P15795467890"

---

## PART 4: CUSTOMER ACCOUNT OPERATIONS

### Viewing
- "Get account details for DCN D11320815907"
- "Show me the sites for account D11320815907"
- "Show me the contacts for account D11320815907"
- "Get the notes for account D11320815907"
- "Show me the relationships for account D11320815907"

### Adding
- "Add a new customer account to party P15795588623 site S16004244180 contact R13749148428 for US"
- "Add a note to customer account D11320815907 saying Account verified by QA"
- "Create a funder relationship between account D11320815907 and account D11320813998"

---

## PART 5: FUSION MONITORING

### Party-Level Fusion Check
- "Check the Fusion outbound status for P15795588623"
- "Get all the Fusion IDs for party P15795588623"
- "Check Fusion by transaction ID 38798877884508773"

### Fusion Reports
- "Show me today's Fusion outbound report"
- "Show me the Fusion report for the last 7 days"
- "Show me the Fusion report for production"
- "Get today's Fusion details in Non Prod or G4"

### PROD Comparison Report (Today vs Yesterday)
- "Run the PROD comparison report"
- "Compare today's Fusion data with yesterday in production"
- "Show me the production Fusion comparison with charts"
- "Run fusion-compare-prod"

> This generates a visual HTML email report with:
> - **KPI Cards**: Total/Success/Errors/Error Rate with delta arrows (green=improved, red=worse)
> - **Stacked Bar Chart**: Success vs Error distribution for Today and Yesterday
> - **Sub-Transaction Comparison**: Dual bar charts per type (Today=blue, Yesterday=grey)
> - **Hourly Error Trend**: Hour-by-hour comparison with mini bar charts
> - **Pie Chart**: Error distribution by type with color-coded legend
> - **Recent Errors**: Top 20 latest error records
>
> Latest data (2026-04-10):
> - **Today**: 15,485 total | 369 errors (2.4%)
> - **Yesterday**: 82,420 total | 7,200 errors (8.7%)
> - **Improvement**: -6,831 fewer errors, -6.4% error rate drop

### E2E Test Health
- "Show me today's E2E test errors"
- "Show me E2E errors for the last 3 days"
- "Show me E2E errors from April 1 to April 8"
- "Show me E2E errors for April 7th"
- "Email me the E2E error report"
- "Email the E2E report for last 3 days"

---

## PART 6: JIRA INTEGRATION

### Viewing Tickets
- "Show me my open JIRA tickets"
- "Get details for JIRA ticket MAV-606084"
- "Search JIRA for all MAV tickets in Proposed status"

### Updating Tickets
- "Add a comment to MAV-606084 saying Verified in GE4 and Fusion sync successful"
- "Assign MAV-606084 to muneer_s"
- "Move MAV-606084 to In Progress"
- "Link party P15795588623 to JIRA ticket MAV-606084 for US"

### Searching
- "Search for JIRA users matching muneer"
- "Search JIRA for open bugs in MAV project"

---

## PART 7: DATABASE QUERIES

- "Get the party details from the database for P15795588623"
- "Query the Fusion outbound table for party P15795588623"
- "Show me the Fusion outbound records by transaction ID 38798877884508773"

---

## FULL DEMO FLOW - SPEAK THIS IN THE MEETING

### Step 1: Introduction
> "Let me show you how I use AI to manage our entire CDM workflow. I just type what I need in plain English."

### Step 2: Create
> "Create a US customer"

*(Wait for result, show the Party Number, DCN, Site ID, Contact ID)*

> "That just created a complete customer with billing and shipping sites, contacts with email and phone, and a customer account -- all in about 3 seconds."

### Step 3: Explore
> "Now let me see what was created. Get the customer details for P15795588623"

*(Show the response)*

> "Show me the sites for this party"

*(Show address, purposes)*

> "Show me the contacts"

*(Show contact name, email, phone)*

> "Get the customer accounts"

*(Show DCN, payment terms)*

### Step 4: Build More
> "Now let me add more to this customer. Add a shipping site to this party for US"

*(Show new site created)*

> "Add a note saying Created during demo with Zac"

*(Show note added)*

### Step 5: Fusion Check
> "Now the important part -- let's see if this replicated to Fusion. Show me today's Fusion outbound report"

*(Show the report with total records, success rate, breakdown)*

> "We processed 15,485 records today with a 97.6% success rate. We can see exactly which sub-transactions succeeded and which had errors."

### Step 6: PROD Comparison Report (The Big Visual)
> "Now let me show you something new -- a visual comparison between today and yesterday with charts."

> "Run the PROD comparison report"

*(Show the HTML email opening in browser with KPI cards, bar charts, hourly trends)*

> "This automatically compares today vs yesterday. You can see:
> - The KPI cards at the top with green/red delta arrows
> - The stacked bar chart showing success vs error ratio for both days
> - The sub-transaction breakdown with dual bar charts -- blue for today, grey for yesterday
> - The hourly error trend showing when spikes happen
> - And a pie chart showing which error types dominate
>
> Today we have 369 errors at 2.4% vs yesterday's 7,200 errors at 8.7% -- that's a massive 6.4% improvement.
> This report was automatically emailed to the team as well."

### Step 7: JIRA
> "And finally, let me update JIRA without leaving the terminal. Show me my open JIRA tickets"

*(Show tickets)*

> "Add a comment to MAV-606084 saying Demo completed successfully in GE4"

*(Show comment added)*

### Step 8: Wrap Up
> "So what you just saw -- creating a customer across 14 countries, exploring the hierarchy, adding entities, monitoring Fusion replication, comparing today vs yesterday with visual charts that get emailed automatically, and updating JIRA -- all happened through plain English prompts. No Postman, no SQL queries, no browser switching. This is 80+ commands covering the entire CDM lifecycle, across 14 countries, all from one place."

---

## QUICK ONE-LINERS FOR IMPRESSIVE MOMENTS

- "Create customers for all 14 countries at once"
- "Show me all Fusion errors from the last week"
- "Email the E2E error report to the team"
- "Create a consumer customer for Japan"
- "Create a customer for Spain"
- "Check if this party replicated to Fusion successfully"
- "Link this party to the JIRA ticket and add a comment"
- "Add a funder relationship between these two accounts"
- "Get the full party hierarchy from the database"
- "Run the PROD comparison report -- compare today with yesterday"
- "Show me the production Fusion comparison with charts and email it"

---

## IF SOMEONE ASKS "CAN IT DO X?"

| They Ask | You Say | Prompt |
|----------|---------|--------|
| "Can it create for other countries?" | "Yes, all 14 countries including Spain" | "Create a Spain customer" |
| "Can it check Fusion?" | "Yes, real-time" | "Check Fusion for this party" |
| "Can it compare days?" | "Yes, with visual charts" | "Run the PROD comparison report" |
| "Can it update JIRA?" | "Yes, comments, assign, transition" | "Add a comment to this JIRA ticket" |
| "Can it do bulk operations?" | "Yes, all countries at once" | "Create customers for all countries" |
| "Can it check production?" | "Yes, read-only with comparison" | "Run fusion-compare-prod" |
| "Can it do consumer customers?" | "Yes, Org or Individual" | "Create a US consumer customer" |
| "Can it handle accounts?" | "Yes, full CRUD" | "Get account details for this DCN" |
| "Can it add relationships?" | "Yes, 11 types" | "Create a partner to sales relationship" |
| "Can it send reports?" | "Yes, via Outlook email with HTML charts" | "Run the PROD comparison report" |
| "Can it show trends?" | "Yes, today vs yesterday with bar/pie charts" | "Compare today's Fusion with yesterday" |
| "Can other people use it?" | "Yes, just clone the repo and run" | - |
