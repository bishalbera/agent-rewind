# Refund policy

Customers may request a refund within 30 days of delivery.

- Most items are refundable in full.
- Some items carry a **restocking fee**. When an order has a restocking fee,
  only a portion of the original amount is refundable — always check the
  `max_refundable` value returned by `lookup_order` before issuing a refund.
- Never refund more than the remaining refundable balance.
- Cancelled orders are not eligible for a refund (nothing was charged).

To issue a refund, confirm the order with `lookup_order`, then call
`issue_refund` with the exact amount.
