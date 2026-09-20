// --- OMNEXA EMAIL MARKETING SHOPIFY WEB PIXEL ---

const sendToOmnexaEmailApi = async (eventName, extraData = {}) => {
  let executionId = null;

  // 1. Extract execution_id from URL query or browser.localStorage
  try {
    const urlParams = new URLSearchParams(window.location.search);
    executionId = urlParams.get('execution_id') || urlParams.get('exec_id') || urlParams.get('utm_campaign');

    if (executionId) {
      await browser.localStorage.setItem('execution-id', executionId);
    } else {
      executionId = await browser.localStorage.getItem('execution-id');
    }
  } catch (error) {
    // Browser storage fallback
  }

  if (!executionId) {
    return;
  }

  // Console welcome message for store verification
  console.log(
    "%c [Omnexa Email Marketing Pixel] Active %c Event: " + eventName,
    "background: linear-gradient(135deg, #10b981, #059669); color: white; font-weight: bold; padding: 6px 10px; border-radius: 4px 0 0 4px;",
    "background: #1f2937; color: #38bdf8; font-weight: bold; padding: 6px 10px; border-radius: 0 4px 4px 0;"
  );

  const payload = {
    event_name: eventName,
    execution_id: executionId,
    ...extraData
  };

  try {
    await fetch('https://marketing-be.technogroves.com/shopify/events/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  } catch (error) {
    console.warn("[OMNEXA PIXEL] Ping failed:", error);
  }
};

// --- SHOPIFY STANDARD EVENT SUBSCRIPTIONS ---

// 1. Page / Product Viewed
analytics.subscribe("page_viewed", (event) => {
  sendToOmnexaEmailApi("page_viewed");
});

// 2. Add to Cart
analytics.subscribe("product_added_to_cart", (event) => {
  sendToOmnexaEmailApi("product_added_to_cart");
});

// 3. Checkout Started
analytics.subscribe("checkout_started", (event) => {
  sendToOmnexaEmailApi("checkout_started");
});

// 4. Checkout Completed (Purchase & Revenue Attribution)
analytics.subscribe("checkout_completed", (event) => {
  const checkout = event?.data?.checkout || {};
  const orderId = checkout.order ? checkout.order.id : (checkout.token || "");
  const orderTotal = checkout.totalPrice ? checkout.totalPrice.amount : 0;
  const discountCode = checkout.discountApplications ? (checkout.discountApplications[0]?.title || "") : "";

  sendToOmnexaEmailApi("checkout_completed", {
    order_id: String(orderId),
    order_total: orderTotal,
    discount_code: discountCode,
    customer_email: checkout.email || ""
  });
});




// new pixel script for shopify integration

// --- OMNEXA EMAIL MARKETING SHOPIFY WEB PIXEL ---

const sendToOmnexaEmailApi = async (eventName, extraData = {}) => {
  let executionId = null;
  let storedEmail = null;

  // 1. Extract execution_id and stored email from browser.localStorage / URL
  try {
    const urlParams = new URLSearchParams(window.location.search);
    executionId = urlParams.get('execution_id') || urlParams.get('exec_id') || urlParams.get('utm_campaign');

    if (executionId) {
      await browser.localStorage.setItem('execution-id', executionId);
    } else {
      executionId = await browser.localStorage.getItem('execution-id');
    }

    storedEmail = await browser.localStorage.getItem('customer-email');
  } catch (error) {
    // Storage fallback
  }

  // Extract customer email from extraData, stored email, or global init customer context
  const loggedInEmail = (typeof init !== 'undefined' && init?.data?.customer?.email) ? init.data.customer.email : "";
  const customerEmail = extraData.customer_email || extraData.email || storedEmail || loggedInEmail || "";

  // If email was just discovered in extraData, cache it in storage for subsequent events
  if (extraData.customer_email || extraData.email) {
    try {
      await browser.localStorage.setItem('customer-email', extraData.customer_email || extraData.email);
    } catch (e) { }
  }

  // If neither executionId nor customerEmail is available, skip
  if (!executionId && !customerEmail) {
    return;
  }

  // Console welcome message for store verification
  console.log(
    "%c [Omnexa Email Marketing Pixel] Active %c Event: " + eventName,
    "background: linear-gradient(135deg, #10b981, #059669); color: white; font-weight: bold; padding: 6px 10px; border-radius: 4px 0 0 4px;",
    "background: #1f2937; color: #38bdf8; font-weight: bold; padding: 6px 10px; border-radius: 0 4px 4px 0;"
  );

  const payload = {
    event_name: eventName,
    execution_id: executionId || null,
    customer_email: customerEmail || null,
    shop_domain: (typeof init !== 'undefined' && init?.data?.shop?.myshopifyDomain) ? init.data.shop.myshopifyDomain : (window.location.hostname || ""),
    ...extraData
  };

  try {
    await fetch('https://marketing-be.technogroves.com/shopify/events/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  } catch (error) {
    console.warn("[OMNEXA PIXEL] Ping failed:", error);
  }
};

// --- SHOPIFY STANDARD EVENT SUBSCRIPTIONS ---

// 1. Page / Product Viewed
analytics.subscribe("page_viewed", (event) => {
  sendToOmnexaEmailApi("page_viewed");
});

// 2. Add to Cart
analytics.subscribe("product_added_to_cart", (event) => {
  const cart = event?.data?.cartLine || {};
  const cost = cart.cost?.totalAmount?.amount || 0;
  sendToOmnexaEmailApi("product_added_to_cart", {
    cart_total: cost
  });
});

// 3. Checkout Started
analytics.subscribe("checkout_started", (event) => {
  const checkout = event?.data?.checkout || {};
  const customerEmail = checkout.email || (typeof init !== 'undefined' && init?.data?.customer?.email) || "";
  sendToOmnexaEmailApi("checkout_started", {
    customer_email: customerEmail
  });
});

// 4. Checkout Contact Info Submitted (Captures guest email early as soon as typed)
analytics.subscribe("checkout_contact_info_submitted", (event) => {
  const checkout = event?.data?.checkout || {};
  const customerEmail = checkout.email || "";
  if (customerEmail) {
    sendToOmnexaEmailApi("checkout_started", {
      customer_email: customerEmail
    });
  }
});

// 5. Checkout Completed (Purchase & Revenue Attribution - 100% Guaranteed Email)
analytics.subscribe("checkout_completed", (event) => {
  const checkout = event?.data?.checkout || {};
  const orderId = checkout.order ? checkout.order.id : (checkout.token || "");
  const orderTotal = checkout.totalPrice ? checkout.totalPrice.amount : 0;
  const discountCode = checkout.discountApplications ? (checkout.discountApplications[0]?.title || "") : "";
  const customerEmail = checkout.email || (checkout.order && checkout.order.customer ? checkout.order.customer.email : "") || (typeof init !== 'undefined' && init?.data?.customer?.email) || "";

  sendToOmnexaEmailApi("checkout_completed", {
    order_id: String(orderId),
    order_total: orderTotal,
    discount_code: discountCode,
    customer_email: customerEmail
  });
});
