const baseUrl = "http://your-management-host:8318";
const token = "your-management-token";
const fileName = "demo@example.com.json";

fetch(`${baseUrl}/v0/management/auth-files/status`, {
  headers: {
    accept: "application/json, text/plain, */*",
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
  },
  body: JSON.stringify({
    name: fileName,
    disabled: true,
  }),
  method: "PATCH",
});

fetch(`${baseUrl}/v0/management/auth-files/status`, {
  headers: {
    accept: "application/json, text/plain, */*",
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
  },
  body: JSON.stringify({
    name: fileName,
    disabled: false,
  }),
  method: "PATCH",
});
