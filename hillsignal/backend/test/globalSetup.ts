import pg from "pg";

export default async function setup() {
  const url = process.env.TEST_DATABASE_URL ?? "postgres://hillsignal:hillsignal@localhost:5432/hillsignal_test";
  const client = new pg.Client({ connectionString: url });
  await client.connect();
  await client.query("DROP SCHEMA public CASCADE; CREATE SCHEMA public;");
  await client.end();
}
