# Arcadia Bank - Cash Transaction Monitoring and Structuring
doc_id: POL-03
doc_type: POLICY

> Synthetic internal document created for the MuleWatch demo. Not legal advice.

## Background
Cash transactions above prescribed thresholds attract additional obligations: quoting of PAN for cash deposits of fifty thousand rupees or more, and cash transaction reporting for large cash transactions within a month. Criminals therefore split cash into amounts just below these thresholds.

## Structuring indicators
1. Repeated cash deposits between Rs 45,000 and Rs 49,999 by the same customer within a short period.
2. Deposits made at several different branches or cities on the same or consecutive days.
3. Multiple related customers depositing just-below-threshold cash which is later consolidated into one account.
4. Onward transfer of the consolidated funds to a corporate or shell beneficiary with generic narration such as "advance for goods".
5. Customer explanations such as "collections from relatives" without supporting documents.

## Required analysis
Investigators should aggregate deposits per customer and per linked group over 30 days, identify the consolidating account, describe the onward destination of funds and record whether PAN was quoted. Structuring is itself a ground of suspicion even when the source of cash is not yet known.
