{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.ns-commute;

  # Remove leading zeros from a string to avoid octal interpretation
  stripLeadingZeros = str:
    let
      stripped = lib.removePrefix "0" str;
    in
    if stripped == "" then "0" else stripped;

  # Parse time offset strings like "15m", "1h", "1h30m" to minutes
  parseOffset = offset:
    let
      # Match hour component (e.g., "1h" or "1h30m")
      hourMatch = builtins.match "([0-9]+)h.*" offset;
      # Match minute component - try full pattern first, then just minutes
      combinedMatch = builtins.match "[0-9]+h([0-9]+)m" offset;  # "1h30m"
      simpleMinMatch = builtins.match "([0-9]+)m" offset;  # "30m"

      hours = if hourMatch != null then lib.toInt (stripLeadingZeros (builtins.elemAt hourMatch 0)) else 0;
      minutes =
        if combinedMatch != null then lib.toInt (stripLeadingZeros (builtins.elemAt combinedMatch 0))
        else if simpleMinMatch != null then lib.toInt (stripLeadingZeros (builtins.elemAt simpleMinMatch 0))
        else 0;
    in
    hours * 60 + minutes;

  # Convert "HH:MM" to total minutes
  timeToMinutes = time:
    let
      parts = lib.splitString ":" time;
      hours = lib.toInt (stripLeadingZeros (builtins.elemAt parts 0));
      minutes = lib.toInt (stripLeadingZeros (builtins.elemAt parts 1));
    in
    hours * 60 + minutes;

  # Convert minutes back to cron time format "MIN HOUR"
  minutesToCronTime = totalMinutes:
    let
      # Handle negative times (wrap to previous day)
      adjustedMinutes = if totalMinutes < 0 then totalMinutes + (24 * 60) else totalMinutes;
      hours = adjustedMinutes / 60;
      mins = lib.mod adjustedMinutes 60;
    in
    "${toString mins} ${toString hours}";

  # Generate systemd timer calendar expression from minutes
  minutesToCalendar = totalMinutes:
    let
      adjustedMinutes = if totalMinutes < 0 then totalMinutes + (24 * 60) else totalMinutes;
      hours = adjustedMinutes / 60;
      mins = lib.mod adjustedMinutes 60;
    in
    "*-*-* ${lib.fixedWidthString 2 "0" (toString hours)}:${lib.fixedWidthString 2 "0" (toString mins)}:00";

  # Generate unique name for a service
  makeServiceName = route: offset:
    let
      departureMinutes = timeToMinutes route.departureTime;
      offsetMinutes = parseOffset offset;
      notificationMinutes = departureMinutes - offsetMinutes;
    in
    "ns-commute-${route.departureStation}-${route.arrivalStation}-${toString notificationMinutes}";

  # Generate all route configurations
  routeConfigs = lib.flatten (
    map (route:
      map (offset: {
        inherit route offset;
        serviceName = makeServiceName route offset;
        departureMinutes = timeToMinutes route.departureTime;
        offsetMinutes = parseOffset offset;
        notificationMinutes = timeToMinutes route.departureTime - parseOffset offset;
      }) route.cronOffsets
    ) cfg.routes
  );

  # Helper to build environment variable assignments for secrets
  makeSecretEnv = { credName, varName, fileOpt, directOpt }:
    if directOpt != null then
      # Direct value - set in systemd unit
      { name = varName; value = directOpt; }
    else if fileOpt != null then
      # File path - will be loaded via LoadCredential, read in ExecStart
      null
    else
      throw "Secret ${credName} not configured";

  # Environment variables for direct values
  directSecretEnvs = lib.filter (x: x != null) [
    (makeSecretEnv {
      credName = "telegram-api-key";
      varName = "TELEGRAM_API_KEY";
      fileOpt = cfg.telegramApiKeyFile;
      directOpt = cfg.telegramApiKey;
    })
    (makeSecretEnv {
      credName = "telegram-chat-id";
      varName = "TELEGRAM_CHAT_ID";
      fileOpt = cfg.telegramChatIdFile;
      directOpt = cfg.telegramChatId;
    })
    (makeSecretEnv {
      credName = "ns-api-key";
      varName = "NS_API_KEY";
      fileOpt = cfg.nsApiKeyFile;
      directOpt = cfg.nsApiKey;
    })
  ];

  # Script to export secrets from LoadCredential
  exportCredsScript = ''
    # Export secrets from systemd credentials if available
    if [ -n "''${CREDENTIALS_DIRECTORY:-}" ]; then
      ${lib.optionalString (cfg.telegramApiKeyFile != null) ''
      export TELEGRAM_API_KEY=$(cat "$CREDENTIALS_DIRECTORY/telegram-api-key")
      ''}
      ${lib.optionalString (cfg.telegramChatIdFile != null) ''
      export TELEGRAM_CHAT_ID=$(cat "$CREDENTIALS_DIRECTORY/telegram-chat-id")
      ''}
      ${lib.optionalString (cfg.nsApiKeyFile != null) ''
      export NS_API_KEY=$(cat "$CREDENTIALS_DIRECTORY/ns-api-key")
      ''}
    fi
  '';

in
{
  options.services.ns-commute = {
    enable = mkEnableOption "NS Commute notification service";

    package = mkOption {
      type = types.package;
      description = "The ns-commute package to use";
    };

    user = mkOption {
      type = types.str;
      default = "ns-commute";
      description = "User to run the cron jobs as";
    };

    telegramApiKey = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Telegram bot API key (insecure, prefer telegramApiKeyFile)";
    };

    telegramApiKeyFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Path to file containing Telegram bot API key";
    };

    telegramChatId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Telegram chat ID to send notifications to (insecure, prefer telegramChatIdFile)";
    };

    telegramChatIdFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Path to file containing Telegram chat ID";
    };

    nsApiKey = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "NS API key from apiportal.ns.nl (insecure, prefer nsApiKeyFile)";
    };

    nsApiKeyFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Path to file containing NS API key";
    };

    routes = mkOption {
      type = types.listOf (types.submodule {
        options = {
          departureStation = mkOption {
            type = types.str;
            example = "Asd";
            description = "Departure station code";
          };

          arrivalStation = mkOption {
            type = types.str;
            example = "Rtd";
            description = "Arrival station code";
          };

          departureTime = mkOption {
            type = types.str;
            example = "08:30";
            description = "Departure time in HH:MM format";
          };

          cronOffsets = mkOption {
            type = types.listOf types.str;
            example = [ "15m" "5m" ];
            description = "List of time offsets before departure to send notifications (e.g., '15m', '1h', '1h30m')";
          };
        };
      });
      default = [ ];
      description = "List of routes to monitor";
    };
  };

  config = mkIf cfg.enable {
    # Create dedicated user for the service
    users.users.${cfg.user} = mkIf (cfg.user == "ns-commute") {
      isSystemUser = true;
      group = cfg.user;
      description = "NS Commute notification service user";
    };

    users.groups.${cfg.user} = mkIf (cfg.user == "ns-commute") {
    };

    # Create systemd services for each route+offset combination
    systemd.services = lib.listToAttrs (
      map (rc: lib.nameValuePair rc.serviceName {
        description = "NS Commute notification for ${rc.route.departureStation} → ${rc.route.arrivalStation} at ${rc.route.departureTime} (${rc.offset} before)";

        serviceConfig = {
          Type = "oneshot";
          User = cfg.user;
          Group = cfg.user;

          # Load secrets via systemd credentials (only for file-based secrets)
          LoadCredential =
            (lib.optional (cfg.telegramApiKeyFile != null) "telegram-api-key:${toString cfg.telegramApiKeyFile}") ++
            (lib.optional (cfg.telegramChatIdFile != null) "telegram-chat-id:${toString cfg.telegramChatIdFile}") ++
            (lib.optional (cfg.nsApiKeyFile != null) "ns-api-key:${toString cfg.nsApiKeyFile}");
        };

        # Set direct value secrets as environment variables
        environment = lib.listToAttrs directSecretEnvs;

        script = ''
          ${exportCredsScript}

          # Run the check (credentials are in environment variables)
          ${cfg.package}/bin/ns-commute-check \
            ${rc.route.departureStation} \
            ${rc.route.arrivalStation} \
            ${rc.route.departureTime}
        '';
      }) routeConfigs
    );

    # Create systemd timers for each service
    systemd.timers = lib.listToAttrs (
      map (rc: lib.nameValuePair rc.serviceName {
        description = "Timer for ${rc.serviceName}";
        wantedBy = [ "timers.target" ];

        timerConfig = {
          OnCalendar = minutesToCalendar rc.notificationMinutes;
          Persistent = true;
        };
      }) routeConfigs
    );

    # Ensure the package is available
    environment.systemPackages = [ cfg.package ];
  };
}
