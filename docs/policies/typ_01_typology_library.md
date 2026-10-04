# Arcadia Bank - Financial Crime Typology Library
doc_id: TYP-01
doc_type: TYPOLOGY

> Synthetic internal document created for the MuleWatch demo. Not legal advice.

## Mule layering network
Victims of cyber-enabled fraud transfer money by UPI or IMPS to collector accounts. Within minutes or hours, collectors forward 90-98 percent of the funds to one or more layer accounts, which forward again to cash-out accounts. Cash-out accounts withdraw cash through ATMs, frequently in other cities, or transfer to crypto exchanges. Accounts are typically new or bought aged accounts, held by students, homemakers or gig workers, and often operated from a handful of shared devices.

## Low-and-slow mule network
A variant designed to evade rules: aged accounts with ordinary profiles, smaller credits, funds held one to three days before onward transfer, and only part of the money withdrawn as cash. Detection relies on network links and combined weak signals rather than any single threshold.

## Cash structuring and consolidation
Several individuals deposit cash just below the PAN threshold at different branches, transfer the totals to a consolidating account, which wires funds to a corporate beneficiary. See POL-03.

## Round-tripping between related businesses
Large funds move in a circle A to B to C and back to A within days, supported by generic invoices, inflating turnover (for example to obtain credit) or disguising the origin of funds. Indicators: circular flows, common directors, generic invoices, near-identical amounts with small decay at each hop.

## Scam types feeding mule networks
Investment or trading-app scams, task-based part-time job scams, "digital arrest" impersonation of officials, loan-app extortion, and phishing or fake KYC update messages.
