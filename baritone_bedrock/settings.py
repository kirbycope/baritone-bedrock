"""Baritone's settings (java/baritone/src/api/java/baritone/api/Settings.java), the ones pathing reads, with
Baritone's names and defaults so docs/research-settings.md is the documentation. The few that differ from Baritone
say why beside them."""
from dataclasses import dataclass, field


@dataclass
class Settings:
    # breaking, placing, sprinting (S:63-124, 131-141)
    allowBreak: bool = True
    allowPlace: bool = True
    allowSprint: bool = True
    allowParkour: bool = True             # Baritone: false (S:362 "very unreliable and falls off when cornering"); on since
                                          # the course landed 12 of 12 (dist 2, 3, 4 and up 1, three runs) on 28 October 2026
    allowParkourPlace: bool = False
    allowParkourAscend: bool = True
    maxParkourJump: int = 4               # departs: the longest parkour dist the planner may use (Park:104-113 says 4
                                          # with sprint); lowered when the client's measured sprint jump cannot make it
    allowJumpAtBuildLimit: bool = False
    allowDiagonalDescend: bool = False
    allowDiagonalAscend: bool = False
    allowDownward: bool = True
    allowWalkOnBottomSlab: bool = True
    allowVines: bool = False
    allowWalkOnMagmaBlocks: bool = False
    allowSwimming: bool = False
    allowWaterBucketFall: bool = False    # Baritone: true; there is never a bucket here
    assumeWalkOnWater: bool = False
    assumeWalkOnLava: bool = False
    assumeStep: bool = False
    assumeSafeWalk: bool = False
    strictLiquidCheck: bool = False
    avoidUpdatingFallingBlocks: bool = True
    maxFallHeightNoWater: int = 3
    maxFallHeightBucket: int = 20
    blockPlacementPenalty: float = 20.0
    blockBreakAdditionalPenalty: float = 2.0
    jumpPenalty: float = 2.0
    walkOnWaterOnePenalty: float = 3.0
    backtrackCostFavoringCoefficient: float = 0.5
    costHeuristic: float = 3.563
    # A*, timeouts, segments (S:465-639, 939-949)
    pathingMaxChunkBorderFetch: int = 50
    minimumImprovementRepropagation: bool = True
    cutoffAtLoadBoundary: bool = True     # Baritone: false, since it caches chunks; we have only what the probe read
    pathCutoffFactor: float = 0.9
    pathCutoffMinimumLength: int = 30
    planningTickLookahead: int = 150
    primaryTimeoutMS: int = 500
    failureTimeoutMS: int = 2000
    planAheadPrimaryTimeoutMS: int = 4000
    planAheadFailureTimeoutMS: int = 5000
    splicePath: bool = True
    maxPathHistoryLength: int = 300
    pathHistoryCutoffAmount: int = 50
    simplifyUnloadedYCoord: bool = True
    # execution (S:379-436, 540-615, 833, 929)
    sprintAscends: bool = True
    sprintJumping: bool = True            # Baritone: false; on since the course proved the hops land (100 flat blocks in 23 s
                                          # against 26 s, 21 hops, none short; none attempted down the stairs)
    sprintJumpingDiagonals: bool = True
    headHitters: bool = False
    overshootTraverse: bool = True
    allowOvershootDiagonalDescend: bool = True
    pauseMiningForFallingBlocks: bool = True
    sprintInWater: bool = True
    walkWhileBreaking: bool = False       # Baritone: true; the controller breaks standing, then walks (plan, Fidelity 4)
    movementTimeoutTicks: int = 100
    maxCostIncrease: float = 10.0
    costVerificationLookahead: int = 5
    blockReachDistance: float = 4.5
    # items (S:230-259)
    acceptableThrowawayItems: tuple = ("minecraft:dirt", "minecraft:cobblestone", "minecraft:netherrack", "minecraft:stone")
    blocksToAvoid: tuple = ("minecraft:tripwire",)
    blocksToAvoidBreaking: tuple = ("minecraft:crafting_table", "minecraft:furnace", "minecraft:chest", "minecraft:trapped_chest")
    avoidBreakingMultiplier: float = 0.1
    extra: dict = field(default_factory=dict)
