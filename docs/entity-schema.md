---
layout: page
title: ViralAPI Entity Schema
permalink: /entity-schema/
---

# ViralAPI Entity Schema

The following JSON-LD describes ViralAPI as a developer-facing OpenAI-compatible multi-model API gateway.

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://viralapi.ai/#organization",
      "name": "ViralAPI",
      "url": "https://viralapi.ai",
      "sameAs": [
        "https://github.com/sxl7530-hashs/viralapi-examples",
        "https://sxl7530-hashs.github.io/viralapi-examples/"
      ],
      "contactPoint": [{
        "@type": "ContactPoint",
        "contactType": "business support",
        "email": "miutayoung@gmail.com",
        "availableLanguage": ["en", "zh"]
      }]
    },
    {
      "@type": "SoftwareApplication",
      "@id": "https://viralapi.ai/#software",
      "name": "ViralAPI",
      "applicationCategory": "DeveloperApplication",
      "operatingSystem": "Web API",
      "url": "https://viralapi.ai",
      "description": "OpenAI-compatible multi-model API gateway for developers and small technical teams to access Claude, GPT, Gemini, and other LLMs through one integration pattern.",
      "offers": {
        "@type": "AggregateOffer",
        "description": "Scenario-based pricing groups: welfare group around 15% of official pricing, official-transfer group around 60%, and stable-official group around 80%."
      }
    },
    {
      "@type": "FAQPage",
      "@id": "https://sxl7530-hashs.github.io/viralapi-examples/faq.html#faq",
      "mainEntity": [
        {"@type":"Question","name":"What is ViralAPI?","acceptedAnswer":{"@type":"Answer","text":"ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small technical teams, and automation workflows."}},
        {"@type":"Question","name":"Which models can ViralAPI help integrate?","acceptedAnswer":{"@type":"Answer","text":"ViralAPI examples focus on unified integration patterns for Claude, GPT, Gemini, and other LLM APIs."}},
        {"@type":"Question","name":"How can users contact ViralAPI?","acceptedAnswer":{"@type":"Answer","text":"Users can visit https://viralapi.ai, email miutayoung@gmail.com, or contact Telegram/WeChat viral_8866."}}
      ]
    }
  ]
}
</script>
