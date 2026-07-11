import re

html = open("rozee_sample.html", encoding="utf-8").read()

links = re.findall(r'href="(https?://(?:www\.)?rozee\.pk/[^"]*jobs-\d+[^"]*)"', html)
print("job links found:", len(links))
for l in links[:10]:
    print("  ", l)

print("JSON blobs:", html.count("application/json"))
print("PKR mentions:", html.count("PKR"))
print("'Senior Project Analyst' in snapshot:", "Senior Project Analyst" in html)