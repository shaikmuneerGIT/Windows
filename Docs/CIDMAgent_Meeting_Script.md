# CIDMAgent - Meeting Presentation Script
## Windsurf/AI Office Hours with Zac Sync Call | 2026-04-10

---

## OPENING (1-2 minutes)

> Hi everyone, thanks for joining. Today I want to walk you through something I've been building that I'm really excited about -- it's called **CIDMAgent**.
>
> In simple terms, CIDMAgent is a **single command-line tool** that replaces the need to manually hit APIs, write Postman requests, query databases, or even open JIRA -- all from one place.
>
> Before I built this, if you wanted to create a customer in our CDM system, you had to:
> - Manually build a JSON payload
> - Set up auth tokens
> - Hit the API through Postman or Swagger
> - Then go to the database to verify
> - Then check Fusion outbound separately
> - Then update JIRA manually
>
> Now? **One command. That's it.**

---

## WHAT IS CIDMAgent? (2-3 minutes)

> So what exactly is CIDMAgent?
>
> It's a **.NET CLI tool** that sits on top of our entire CDM ecosystem. It has **80 commands** across **17 categories** that cover the full customer data lifecycle.
>
> Let me break that down into **5 pillars**:
>
> **Pillar 1 - Customer Creation:**
> We can create customers across **13 countries** -- US, China, India, Germany, Brazil, Italy, Japan, Malaysia, Mexico, Saudi Arabia, Great Britain, Belgium, and New Zealand. The tool auto-generates realistic test data -- proper addresses, ZIP codes, tax IDs, phone numbers with correct country codes -- everything country-specific.
>
> **Pillar 2 - Full CRUD Operations:**
> Every entity in our CDM model -- customers, sites, contacts, notes, relationships, customer accounts, account sites, account contacts, account notes, account relationships -- all of them have GET, POST, PATCH support through simple commands.
>
> **Pillar 3 - Fusion Monitoring:**
> We can check Fusion Outbound replication status for any party, get detailed reports on success rates, error breakdowns by sub-transaction type, and even track E2E test health -- all querying the CPD_PUBSUB database directly.
>
> **Pillar 4 - JIRA Integration:**
> Search tickets, add comments, assign tickets, transition statuses, and even link party numbers to JIRA tickets -- without ever leaving the terminal.
>
> **Pillar 5 - Database Queries:**
> Direct Oracle database access to CPD_PARTY, CPD_CUSTACCT, and CPD_PUBSUB schemas for party lookups, Fusion tracking, and transaction log analysis.

---

## LIVE DEMO - PART 1: Creating a Customer (3-4 minutes)

> Let me show you how this works. I'll create a US customer right now, live.
>
> The command is simple:

```
dotnet run --project CIDMAgent -- create-customer US
```

> That's it. One line.
>
> What happens behind the scenes:
> - It loads country-specific rules for the US -- BU ID 108401
> - Generates a realistic company name using Bogus faker library
> - Creates a proper US address -- pulls from a real ZIP code database, picks a valid city, state, ZIP combination
> - Generates contact details -- email and phone with US country code +1
> - Builds the full JSON payload with billing and shipping site purposes
> - Authenticates with our API gateway
> - POSTs to `/v1/customers`
> - And returns the created entity IDs
>
> *(Run the command and show output)*
>
> As you can see, we got back:
> - **Party Number** -- that's our P-number
> - **DCN** -- the Dell Customer Number, our D-number
> - **Site ID** -- the S-number
> - **Contact ID** -- the R-number
>
> All created in about 3 seconds. No Postman. No manual JSON. No copy-pasting.
>
> And if I wanted a Consumer customer instead of Organization, I just add "I":

```
dotnet run --project CIDMAgent -- create-customer US I
```

> Want all 13 countries at once? One command:

```
dotnet run --project CIDMAgent -- create-all
```

---

## LIVE DEMO - PART 2: Exploring the Hierarchy (2-3 minutes)

> Now let's look at what we just created. I can pull the full party hierarchy:

```
dotnet run --project CIDMAgent -- get-customer P15795588623
dotnet run --project CIDMAgent -- get-sites P15795588623
dotnet run --project CIDMAgent -- get-contacts P15795588623
dotnet run --project CIDMAgent -- get-customer-accounts P15795588623
```

> Each of these hits our REST API and returns a clean, formatted JSON response.
>
> The hierarchy looks like this:
> - **Party** at the top -- with name, segment (COMM), status (Active), contact methods
>   - **Site** -- with the address in Atlanta, GA, billing + shipping purposes
>   - **Contact** -- with email and phone
>   - **Customer Account** -- with DCN, payment terms, tax profile
>
> This is the complete CDM data model, navigable through simple commands.

---

## LIVE DEMO - PART 3: Building on Top (2-3 minutes)

> But creating is just the start. Let me show you how we build on top of an existing party.
>
> **Add another site:**

```
dotnet run --project CIDMAgent -- add-shipping-site P15795588623 US
```

> **Add a contact to a site:**

```
dotnet run --project CIDMAgent -- add-party-contact P15795588623 S16004244180 US
```

> **Add a note:**

```
dotnet run --project CIDMAgent -- add-party-note P15795588623 US "QA validation complete"
```

> **Extend a billing contact to shipping:**

```
dotnet run --project CIDMAgent -- extend-contact P15795588623 S16004244180 R13749148428
```

> **Create a relationship between two parties:**

```
dotnet run --project CIDMAgent -- add-party-relationship P15795588623 P15795467890 partnertosales
```

> We support 11 relationship types -- partner-to-sales, sales-to-partner, bill-to, ship-to, funder, and more.
>
> Every single one of these auto-generates a valid payload. No JSON files to maintain.

---

## LIVE DEMO - PART 4: Customer Accounts (2 minutes)

> The same pattern works for Customer Accounts. These use DCN instead of Party Number:

```
dotnet run --project CIDMAgent -- get-account D11320815907
dotnet run --project CIDMAgent -- get-acct-sites D11320815907
dotnet run --project CIDMAgent -- get-acct-contacts D11320815907
dotnet run --project CIDMAgent -- get-acct-notes D11320815907
dotnet run --project CIDMAgent -- get-acct-relationships D11320815907
```

> We can also add account-level entities:

```
dotnet run --project CIDMAgent -- add-cust-acct-site D11320815907 S16004244180 R13749148428 US
dotnet run --project CIDMAgent -- add-cust-acct-note D11320815907 "Account verified"
dotnet run --project CIDMAgent -- add-cust-acct-relationship D11320815907 D11320813998 funder
```

> Full CRUD on both Party-level AND Account-level entities.

---

## LIVE DEMO - PART 5: Fusion Monitoring (3-4 minutes)

> This is one of the most powerful features. After we create a customer, the data needs to replicate to Oracle Fusion Cloud. We can monitor that in real time.
>
> **Check Fusion for a specific party:**

```
dotnet run --project CIDMAgent -- check-fusion P15795588623
```

> This queries the CPD_PUBSUB.FUSION_OUTBOUND table and shows every sub-transaction -- party_org, location, party_site_siteuses, cust_acct, and so on -- with their status: success or error.
>
> **Get a full Fusion report for the last 24 hours:**

```
dotnet run --project CIDMAgent -- fusion-report
```

> *(Show the output)*
>
> Look at this -- it gives us:
> - **Total records processed** -- today we have 5,499
> - **Success rate** -- 98.8%
> - **Breakdown by sub-transaction type** -- we can see exactly which types are succeeding and which have errors
> - **Recent errors** -- top 20 with party IDs and timestamps
> - **Error details** -- the actual header data for debugging
>
> This is data that used to require logging into the database, writing SQL queries, and manually analyzing results. Now it's one command.
>
> **E2E test health:**

```
dotnet run --project CIDMAgent -- e2e-errors
```

> Shows all E2E automation errors for today. We can also do date ranges:

```
dotnet run --project CIDMAgent -- e2e-errors -3          # Last 3 days
dotnet run --project CIDMAgent -- e2e-errors 2026-04-01 2026-04-08   # Date range
```

> And we can **email the report** to the team:

```
dotnet run --project CIDMAgent -- e2e-report
```

---

## LIVE DEMO - PART 6: JIRA Integration (2 minutes)

> Last but not least -- JIRA integration. No more switching between terminal and browser.
>
> **Check my open tickets:**

```
dotnet run --project CIDMAgent -- my-jira
```

> **Get ticket details:**

```
dotnet run --project CIDMAgent -- get-jira MAV-606084
```

> **Search with JQL:**

```
dotnet run --project CIDMAgent -- search-jira "project=MAV AND status=Proposed"
```

> **Add a comment after testing:**

```
dotnet run --project CIDMAgent -- comment-jira MAV-606084 "Verified in GE4, Fusion sync successful"
```

> **Link a party to a ticket:**

```
dotnet run --project CIDMAgent -- link-party MAV-606084 P15795588623 US
```

> **Transition the ticket:**

```
dotnet run --project CIDMAgent -- transition-jira MAV-606084 "In Progress"
```

> So the entire workflow -- create customer, verify, check Fusion, update JIRA -- all happens without leaving the terminal.

---

## TECHNICAL ARCHITECTURE (2 minutes)

> A quick word on how it's built:
>
> - **Language:** C# / .NET 6
> - **API Communication:** HttpClient with auto-token management
> - **Test Data Generation:** Bogus faker library with locale-specific generators (en_US, zh_CN, ja, etc.)
> - **Database:** Oracle.ManagedDataAccess with TCPS connections to CPD_PARTY, CPD_CUSTACCT, CPD_PUBSUB
> - **Address Data:** Country-specific CSV files for realistic addresses (ZIP codes, cities, states)
> - **Rules Engine:** JSON-based country rules for tax types, payment terms, contact methods
> - **Output:** All requests and responses saved to TestLogs with timestamps
>
> It connects to **4 database regions** -- AMER, EMEA, APJ, LATAM -- across GE1 through GE4 environments, plus Production.

---

## IMPACT & VALUE (2 minutes)

> Let me talk about the real impact:
>
> **Before CIDMAgent:**
> - Creating one customer: 10-15 minutes (build JSON, set up Postman, hit API, verify)
> - Creating customers for all 13 countries: over 2 hours
> - Checking Fusion status: 5-10 minutes (connect to DB, write SQL, analyze)
> - Full E2E cycle with JIRA update: 30+ minutes
>
> **After CIDMAgent:**
> - Creating one customer: **3 seconds**
> - All 13 countries: **under 1 minute**
> - Fusion status check: **5 seconds**
> - Full E2E cycle with JIRA: **under 2 minutes**
>
> That's roughly a **90% reduction** in manual effort for test data creation and monitoring.
>
> And because the payloads are auto-generated with proper country-specific rules, we get **consistent, validated test data** every time. No more copy-paste errors or stale JSON files.
>
> The tool also saves every request and response with timestamps, so we have a complete **audit trail** for debugging and reporting.

---

## WHAT'S NEXT (1 minute)

> Going forward, here's what I'm planning:
>
> 1. **More countries** -- adding NZ and other regions as they come online
> 2. **Batch operations** -- bulk create/update from CSV files
> 3. **Automated E2E pipelines** -- scheduled Fusion health checks with Slack/email alerts
> 4. **Interactive mode** -- guided wizard for complex multi-step operations
> 5. **Dashboard integration** -- feeding CIDMAgent data into team dashboards
>
> The goal is to make this the **single entry point** for all CDM operations -- development, testing, monitoring, and incident response.

---

## CLOSING (30 seconds)

> To wrap up -- CIDMAgent gives us:
>
> - **80 commands** covering the entire CDM lifecycle
> - **13 countries** with auto-generated, country-specific test data
> - **Fusion monitoring** with real-time replication tracking
> - **JIRA integration** for seamless workflow management
> - **Database access** for deep-dive analysis
> - All from **one terminal, one tool, one command**.
>
> I'm planning to share this with the broader team and would love to get it adopted across our QA and development workflows.
>
> Happy to take any questions or do a deeper dive into any specific area.
>
> Thank you!

---

## Q&A PREP -- Likely Questions & Answers

**Q: How does authentication work?**
> A: The tool reads API credentials from launchSettings.json, automatically obtains OAuth tokens, and manages token refresh. No manual token handling needed.

**Q: Can other team members use this?**
> A: Yes, they just need to clone the repo and have .NET 6 SDK installed. The tool reads environment config from launchSettings.json, so each person can point to their preferred environment.

**Q: What if an API call fails?**
> A: The tool shows the HTTP status code and full error response. All requests and responses are saved to TestLogs with timestamps for debugging.

**Q: Does it work with Production?**
> A: Yes, we have Production database connections (read-only via CPD_READ_ONLY) for Fusion monitoring and reporting. The API commands work against whatever gateway URL is configured.

**Q: How is this different from using Postman?**
> A: Three key advantages: (1) Auto-generated payloads -- no manual JSON building, (2) Country-specific rules baked in -- tax types, address formats, etc., (3) Everything is scriptable and repeatable -- you can chain commands or put them in a CI/CD pipeline.

**Q: What about data cleanup?**
> A: We can deactivate customers and sites through PATCH commands. The tool supports status updates for all entity types.

**Q: How do you handle different environments (GE1, GE2, GE3, GE4)?**
> A: The TEST_ENVIRONMENT variable in launchSettings.json controls which environment we target. The tool automatically selects the correct database connections and API endpoints based on this setting.

**Q: Can we integrate this into our CI/CD pipelines?**
> A: Absolutely. Since it's a CLI tool, any CI/CD system can call it. The exit codes and structured output make it easy to parse results and fail builds on errors.

---

## TIMING GUIDE

| Section | Duration | Running Total |
|---------|----------|---------------|
| Opening | 1-2 min | 2 min |
| What is CIDMAgent | 2-3 min | 5 min |
| Demo: Create Customer | 3-4 min | 9 min |
| Demo: Explore Hierarchy | 2-3 min | 12 min |
| Demo: Build on Top | 2-3 min | 15 min |
| Demo: Customer Accounts | 2 min | 17 min |
| Demo: Fusion Monitoring | 3-4 min | 21 min |
| Demo: JIRA Integration | 2 min | 23 min |
| Technical Architecture | 2 min | 25 min |
| Impact & Value | 2 min | 27 min |
| What's Next | 1 min | 28 min |
| Closing | 30 sec | 28.5 min |
| Q&A | 5-10 min | ~35-40 min |

> **Total: ~30 minutes presentation + 10 minutes Q&A**
>
> **If short on time:** Skip Technical Architecture and Customer Accounts sections (saves ~4 minutes)
> **If very short (15 min):** Do Opening + What is CIDMAgent + Create Customer demo + Fusion demo + Impact + Closing
