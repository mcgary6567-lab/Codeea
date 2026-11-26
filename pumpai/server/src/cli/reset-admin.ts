/**
 * Locked out? Set a new password for a user from the server (needs shell access, so only the installer / owner can):
 *   sudo -u pumpai bash -c 'set -a; . /etc/pumpai.env; cd /opt/pumpai/server && node dist/cli/reset-admin.js owner@email.com NewPassword123'
 *   docker compose exec app node dist/cli/reset-admin.js owner@email.com NewPassword123
 * Without an email it lists the admins.
 */
import bcrypt from "bcryptjs";
import { all, get, run, closeDb } from "../db.js";

const [email, password] = process.argv.slice(2);
if (!email) {
  console.log("Admins:", all("SELECT email, name, active FROM users WHERE role='admin'"));
  console.log("Usage: node dist/cli/reset-admin.js <email> <new password (8+ characters)>");
} else {
  const u = get("SELECT id, name, role FROM users WHERE email=?", email.toLowerCase().trim());
  if (!u) { console.error(`No user with email ${email}`); process.exitCode = 1; }
  else if (!password || password.length < 8) { console.error("Give a new password of at least 8 characters"); process.exitCode = 1; }
  else {
    run("UPDATE users SET password_hash=?, pw_fails=0, pw_locked_until=NULL, pin_fails=0, pin_locked_until=NULL, active=1, token_version=token_version+1 WHERE id=?", bcrypt.hashSync(password, 10), u.id);
    console.log(`Password changed for ${u.name} (${u.role}). Old sessions are signed out.`);
  }
}
closeDb();
