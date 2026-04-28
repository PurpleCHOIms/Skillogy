import neo4j from "neo4j-driver";
import type { Driver } from "neo4j-driver";

const DEFAULT_URI = "bolt://localhost:7687";
const DEFAULT_USER = "neo4j";
const DEFAULT_PASS = "skillrouter";

let _driver: Driver | undefined;

export function getDriver(): Driver {
  if (!_driver) {
    const uri = process.env.NEO4J_URI ?? DEFAULT_URI;
    const user = process.env.NEO4J_USER ?? DEFAULT_USER;
    const pwd = process.env.NEO4J_PASSWORD ?? DEFAULT_PASS;
    _driver = neo4j.driver(uri, neo4j.auth.basic(user, pwd));
  }
  return _driver;
}

export async function closeDriver(): Promise<void> {
  if (_driver) {
    await _driver.close();
    _driver = undefined;
  }
}
