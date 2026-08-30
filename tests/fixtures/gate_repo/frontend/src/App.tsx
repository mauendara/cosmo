import { useEffect, useState } from "react";
import { backendUrl } from "./greeting";

export function App() {
  const [message, setMessage] = useState("loading...");

  useEffect(() => {
    fetch(`${backendUrl()}/api/hello`)
      .then((res) => res.json())
      .then((body: { message: string }) => setMessage(body.message))
      .catch(() => setMessage("error"));
  }, []);

  return <div data-testid="greeting">{message}</div>;
}
